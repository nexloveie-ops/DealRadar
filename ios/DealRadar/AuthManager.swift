import Foundation
import SwiftUI
import AuthenticationServices
import CryptoKit
import FirebaseCore
import FirebaseAuth
import GoogleSignIn

/// Signs members in with the same Firebase accounts as dealradar.ie.
@MainActor
final class AuthManager: ObservableObject {
    @Published private(set) var user: User?
    @Published var error: String?
    @Published var busy = false
    private var handle: AuthStateDidChangeListenerHandle?
    private var currentNonce: String?

    init() {
        user = Auth.auth().currentUser
        handle = Auth.auth().addStateDidChangeListener { [weak self] _, user in
            Task { @MainActor in self?.user = user }
        }
    }

    var signedIn: Bool { user != nil }

    // MARK: Google

    func signInWithGoogle() async {
        guard let clientID = FirebaseApp.app()?.options.clientID else { error = "Google sign-in isn't set up."; return }
        guard let root = Self.topViewController() else { return }
        GIDSignIn.sharedInstance.configuration = GIDConfiguration(clientID: clientID)
        busy = true
        defer { busy = false }
        do {
            let result = try await GIDSignIn.sharedInstance.signIn(withPresenting: root)
            guard let idToken = result.user.idToken?.tokenString else { error = "Google didn't return a sign-in token."; return }
            let credential = GoogleAuthProvider.credential(withIDToken: idToken, accessToken: result.user.accessToken.tokenString)
            try await Auth.auth().signIn(with: credential)
            error = nil
        } catch let e as NSError where e.domain == kGIDSignInErrorDomain && e.code == GIDSignInError.canceled.rawValue {
            // The member closed the Google window.
        } catch {
            self.error = "Google sign-in didn't work: \(error.localizedDescription)"
        }
    }

    // MARK: Apple

    func prepare(_ request: ASAuthorizationAppleIDRequest) {
        let nonce = Self.randomNonce()
        currentNonce = nonce
        request.requestedScopes = [.email, .fullName]
        request.nonce = Self.sha256(nonce)
    }

    func finish(_ result: Result<ASAuthorization, Error>) async {
        switch result {
        case .failure(let e):
            if (e as? ASAuthorizationError)?.code != .canceled { error = "Apple sign-in didn't work: \(e.localizedDescription)" }
        case .success(let auth):
            guard let cred = auth.credential as? ASAuthorizationAppleIDCredential,
                  let tokenData = cred.identityToken, let token = String(data: tokenData, encoding: .utf8),
                  let nonce = currentNonce else { error = "Apple didn't return a sign-in token."; return }
            busy = true
            defer { busy = false }
            do {
                let credential = OAuthProvider.appleCredential(withIDToken: token, rawNonce: nonce, fullName: cred.fullName)
                try await Auth.auth().signIn(with: credential)
                error = nil
            } catch {
                self.error = "Apple sign-in didn't work: \(error.localizedDescription)"
            }
        }
    }

    /// Deletes the account: the member's data on dealradar.ie, then the sign-in account itself.
    /// Apple and Firebase ask for a fresh sign-in first; for Sign in with Apple the Apple token is also revoked.
    func deleteAccount() async -> Bool {
        guard let user = Auth.auth().currentUser else { return false }
        busy = true
        defer { busy = false }
        let apple = user.providerData.contains { $0.providerID == "apple.com" }
        do {
            if apple {
                let nonce = Self.randomNonce()
                let cred = try await AppleIDRequester().request(nonce: Self.sha256(nonce))
                guard let tokenData = cred.identityToken, let token = String(data: tokenData, encoding: .utf8),
                      let codeData = cred.authorizationCode, let code = String(data: codeData, encoding: .utf8) else {
                    error = "Apple didn't confirm your sign-in."; return false
                }
                try await user.reauthenticate(with: OAuthProvider.appleCredential(withIDToken: token, rawNonce: nonce, fullName: nil))
                let _: Empty = try await API.call("api/me", method: "DELETE")
                try await Auth.auth().revokeToken(withAuthorizationCode: code)
            } else {
                let _: Empty = try await API.call("api/me", method: "DELETE")
            }
            do {
                try await user.delete()
            } catch let e as NSError where e.code == AuthErrorCode.requiresRecentLogin.rawValue && !apple {
                // Google: confirm the account again, then delete.
                guard let clientID = FirebaseApp.app()?.options.clientID, let root = Self.topViewController() else { throw e }
                GIDSignIn.sharedInstance.configuration = GIDConfiguration(clientID: clientID)
                let r = try await GIDSignIn.sharedInstance.signIn(withPresenting: root)
                guard let idToken = r.user.idToken?.tokenString else { throw e }
                try await user.reauthenticate(with: GoogleAuthProvider.credential(withIDToken: idToken, accessToken: r.user.accessToken.tokenString))
                try await user.delete()
            }
            signOut()
            error = nil
            return true
        } catch let e as ASAuthorizationError where e.code == .canceled {
            return false
        } catch let e as NSError where e.domain == kGIDSignInErrorDomain && e.code == GIDSignInError.canceled.rawValue {
            return false
        } catch {
            self.error = "Your account couldn't be deleted: \(error.localizedDescription)"
            return false
        }
    }

    func signOut() {
        GIDSignIn.sharedInstance.signOut()
        try? Auth.auth().signOut()
    }

    // MARK: helpers

    static func topViewController() -> UIViewController? {
        let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first { $0.activationState == .foregroundActive }
            ?? UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
        var vc = scene?.windows.first(where: \.isKeyWindow)?.rootViewController ?? scene?.windows.first?.rootViewController
        while let p = vc?.presentedViewController { vc = p }
        return vc
    }

    static func randomNonce(length: Int = 32) -> String {
        let chars = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._")
        var out = ""
        var generator = SystemRandomNumberGenerator()
        for _ in 0..<length { out.append(chars[Int(generator.next() % UInt64(chars.count))]) }
        return out
    }

    static func sha256(_ s: String) -> String {
        SHA256.hash(data: Data(s.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

/// Asks Apple to confirm the signed-in Apple ID (used before deleting an account).
@MainActor
final class AppleIDRequester: NSObject, ASAuthorizationControllerDelegate, ASAuthorizationControllerPresentationContextProviding {
    private var continuation: CheckedContinuation<ASAuthorizationAppleIDCredential, Error>?
    private var keep: AppleIDRequester?

    func request(nonce: String) async throws -> ASAuthorizationAppleIDCredential {
        let req = ASAuthorizationAppleIDProvider().createRequest()
        req.nonce = nonce
        let c = ASAuthorizationController(authorizationRequests: [req])
        c.delegate = self
        c.presentationContextProvider = self
        keep = self
        return try await withCheckedThrowingContinuation { cont in
            continuation = cont
            c.performRequests()
        }
    }

    nonisolated func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        MainActor.assumeIsolated { AuthManager.topViewController()?.view.window ?? UIWindow() }
    }

    nonisolated func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        MainActor.assumeIsolated {
            if let c = authorization.credential as? ASAuthorizationAppleIDCredential { continuation?.resume(returning: c) }
            else { continuation?.resume(throwing: ASAuthorizationError(.failed)) }
            continuation = nil; keep = nil
        }
    }

    nonisolated func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        MainActor.assumeIsolated { continuation?.resume(throwing: error); continuation = nil; keep = nil }
    }
}

struct SignInView: View {
    var title = "Your own deal list"
    var message = "Sign in to follow the shops and networks you use, add your own search words, and watch the products you want."

    var body: some View {
        ScrollView { SignInCard(title: title, message: message).padding(16) }
            .background(Color.drShelf)
    }
}

struct SignInCard: View {
    @EnvironmentObject var auth: AuthManager
    var title = "Your own deal list"
    var message = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.title.weight(.bold)).foregroundStyle(Color.drInk)
            Text(message).foregroundStyle(Color.drMuted)

            SignInWithAppleButton(.continue) { auth.prepare($0) } onCompletion: { r in Task { await auth.finish(r) } }
                .signInWithAppleButtonStyle(.black)
                .frame(height: 50)
                .clipShape(RoundedRectangle(cornerRadius: 8))

            Button { Task { await auth.signInWithGoogle() } } label: {
                HStack(spacing: 10) {
                    Text("G").font(.title3.weight(.bold)).foregroundStyle(Color.drBlue)
                    Text("Continue with Google").font(.headline)
                }
                .frame(maxWidth: .infinity, minHeight: 50)
                .background(Color.white, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.drLine))
            }
            .foregroundStyle(Color.drInk)

            Text("Signed up on dealradar.ie with an email link? Use Continue with Google with the same Gmail address, or sign in on the website for now.")
                .font(.footnote).foregroundStyle(Color.drMuted)

            if auth.busy { ProgressView().frame(maxWidth: .infinity) }
            if let e = auth.error {
                Text(e).font(.footnote).foregroundStyle(Color(hex: 0xA32D2D))
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(hex: 0xFCEBEB), in: RoundedRectangle(cornerRadius: 6))
            }
        }
        .padding(24)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 10))
    }
}
