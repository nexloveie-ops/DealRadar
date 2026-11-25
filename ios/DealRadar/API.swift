import Foundation
import FirebaseAuth

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Talks to the same API as dealradar.ie. Member calls carry the Firebase ID token.
enum API {
    static let base = URL(string: "https://www.dealradar.ie")!

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    static func get<T: Decodable>(_ path: String, auth: Bool = false) async throws -> T {
        try await send(path, method: "GET", body: Optional<[String: String]>.none, auth: auth)
    }

    static func send<T: Decodable, B: Encodable>(_ path: String, method: String, body: B?, auth: Bool = true) async throws -> T {
        try await request(path, method: method, body: try body.map { try JSONEncoder().encode($0) }, auth: auth)
    }

    /// Member call with a JSON body written as a dictionary, e.g. ["min_discount": 30].
    static func call<T: Decodable>(_ path: String, method: String, json: [String: Any]? = nil) async throws -> T {
        try await request(path, method: method, body: try json.map { try JSONSerialization.data(withJSONObject: $0) }, auth: true)
    }

    private static func request<T: Decodable>(_ path: String, method: String, body: Data?, auth: Bool) async throws -> T {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = method
        req.timeoutInterval = 60
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if auth {
            guard let user = Auth.auth().currentUser else { throw APIError(message: "Sign in first.") }
            let token = try await user.getIDToken()
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if status == 401 { throw APIError(message: "Your sign-in has expired. Sign in again.") }
        guard (200..<300).contains(status) else {
            let msg = (try? decoder.decode(APIErrorBody.self, from: data))?.error
            throw APIError(message: msg ?? "Something went wrong (\(status)). Try again.")
        }
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError(message: "The app couldn't read the reply from Deal Radar. Update the app or try again.") }
    }

    /// Counts a tap on a shop link, as the website does. Never blocks opening the shop.
    static func recordClick(_ dealId: String) {
        Task.detached {
            var req = URLRequest(url: base.appendingPathComponent("api/click"))
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let token = try? await Auth.auth().currentUser?.getIDToken()
            req.httpBody = try? JSONSerialization.data(withJSONObject: ["id": dealId, "token": token as Any])
            _ = try? await URLSession.shared.data(for: req)
        }
    }
}

struct Empty: Codable {}
