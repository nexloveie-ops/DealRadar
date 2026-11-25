import SwiftUI
import FirebaseCore
import GoogleSignIn

@main
struct DealRadarApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
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
    @ObservedObject private var push = PushManager.shared
    @State private var tab = 0

    var body: some View {
        TabView(selection: $tab) {
            DealsView().tabItem { Label("Deals", systemImage: "tag") }.tag(0)
            MyDealsView().tabItem { Label("My deals", systemImage: "star") }.tag(1)
            SettingsView().tabItem { Label("Settings", systemImage: "slider.horizontal.3") }.tag(2)
        }
        .onChange(of: push.openTab) { _, t in
            // A tapped notification: price alerts and word alerts open My deals (fresh), the daily summary opens Deals.
            guard let t else { return }
            tab = t
            if t == 1 { member.myDealsStale = true; Task { await member.loadMyDeals() } }
            push.openTab = nil
        }
        .sheet(isPresented: $push.showIntro) { NotificationsIntro() }
        .task { await push.maybeShowIntro() }
        .task(id: auth.user?.uid) {
            // Load the member's data whenever someone signs in; clear it on sign-out.
            if auth.signedIn { await member.load(); await member.loadMyDeals() } else { member.reset() }
            await push.register()   // link this phone to the member, or back to anonymous after signing out
        }
    }
}
