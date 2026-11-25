import SwiftUI
import FirebaseCore
import GoogleSignIn

@main
struct DealRadarApp: App {
    @StateObject private var auth: AuthManager
    @StateObject private var member = MemberStore()

    init() {
        FirebaseApp.configure()
        _auth = StateObject(wrappedValue: AuthManager())
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(auth)
                .environmentObject(member)
                .tint(.drBlue)
                .preferredColorScheme(.light)
                .onOpenURL { GIDSignIn.sharedInstance.handle($0) }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var auth: AuthManager
    @EnvironmentObject var member: MemberStore

    var body: some View {
        TabView {
            DealsView().tabItem { Label("Deals", systemImage: "tag") }
            MyDealsView().tabItem { Label("My deals", systemImage: "star") }
            SettingsView().tabItem { Label("Settings", systemImage: "slider.horizontal.3") }
        }
        .task(id: auth.user?.uid) {
            // Load the member's data whenever someone signs in; clear it on sign-out.
            if auth.signedIn { await member.load(); await member.loadMyDeals() } else { member.reset() }
        }
    }
}
