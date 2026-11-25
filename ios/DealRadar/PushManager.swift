import Foundation
import SwiftUI
import UIKit
import UserNotifications
import FirebaseAuth
import FirebaseMessaging

/// Notifications: asks permission, sends this phone's push token and choices to dealradar.ie,
/// and opens the right tab when a notification is tapped.
@MainActor
final class PushManager: NSObject, ObservableObject {
    static let shared = PushManager()

    enum Pref: String, CaseIterable {
        case watch, words, daily
        var title: String {
            switch self {
            case .watch: "Price alerts for products I watch"
            case .words: "New deals for my search words"
            case .daily: "Daily summary of new deals"
            }
        }
        var note: String {
            switch self {
            case .watch: "When a watched product meets your target, after any price check."
            case .words: "Around 7pm, when there are new deals for your words."
            case .daily: "Around 7pm, when there are new deals. Only if you follow no words or products."
            }
        }
    }

    @Published private(set) var status: UNAuthorizationStatus = .notDetermined
    @Published var prefs: [Pref: Bool]
    /// Tab to show after a notification is tapped: 0 Deals, 1 My deals.
    @Published var openTab: Int?
    @Published var showIntro = false

    private var token: String?
    private let defaults = UserDefaults.standard

    override init() {
        var p: [Pref: Bool] = [:]
        for k in Pref.allCases { p[k] = defaults.object(forKey: "push.\(k.rawValue)") as? Bool ?? true }
        prefs = p
        super.init()
    }

    var enabled: Bool { status == .authorized || status == .provisional || status == .ephemeral }

    func refreshStatus() async {
        status = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        if enabled { UIApplication.shared.registerForRemoteNotifications() }
    }

    /// Shows the explanation page once, the first time the app runs, before iOS asks.
    func maybeShowIntro() async {
        await refreshStatus()
        if status == .notDetermined && !defaults.bool(forKey: "push.introShown") { showIntro = true }
    }

    func requestPermission() async {
        defaults.set(true, forKey: "push.introShown")
        showIntro = false
        let granted = (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        await refreshStatus()
        if granted { UIApplication.shared.registerForRemoteNotifications() }
    }

    func notNow() {
        defaults.set(true, forKey: "push.introShown")
        showIntro = false
    }

    func set(_ pref: Pref, _ on: Bool) {
        prefs[pref] = on
        defaults.set(on, forKey: "push.\(pref.rawValue)")
        Task { await register() }
    }

    func received(token: String?) {
        guard let token, token != self.token else { return }
        self.token = token
        Task { await register() }
    }

    /// Saves the token and choices on dealradar.ie; linked to the member when signed in, anonymous otherwise.
    func register() async {
        guard let token else { return }
        var req = URLRequest(url: API.base.appendingPathComponent("api/push/device"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let user = Auth.auth().currentUser, let id = try? await user.getIDToken() {
            req.setValue("Bearer \(id)", forHTTPHeaderField: "Authorization")
        }
        let body: [String: Any] = ["token": token, "platform": "ios",
                                   "prefs": Dictionary(uniqueKeysWithValues: prefs.map { ($0.key.rawValue, $0.value) })]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    func handleTap(_ info: [AnyHashable: Any]) {
        switch info["type"] as? String {
        case "watch", "words": openTab = 1
        default: openTab = 0
        }
    }
}

extension PushManager: MessagingDelegate, UNUserNotificationCenterDelegate {
    nonisolated func messaging(_ messaging: Messaging, didReceiveRegistrationToken fcmToken: String?) {
        Task { @MainActor in PushManager.shared.received(token: fcmToken) }
    }

    // Show notifications while the app is open, too.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions { [.banner, .sound, .list] }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        await MainActor.run { PushManager.shared.handleTap(info) }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        Messaging.messaging().delegate = PushManager.shared
        UNUserNotificationCenter.current().delegate = PushManager.shared
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Messaging.messaging().apnsToken = deviceToken
    }
}

/// Shown once before iOS asks for permission, so people know what they will get.
struct NotificationsIntro: View {
    @ObservedObject var push = PushManager.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Image(systemName: "bell.badge.fill").font(.system(size: 44)).foregroundStyle(Color.drBlue).padding(.top, 30)
            Text("Get deal alerts").font(.largeTitle.weight(.bold)).foregroundStyle(Color.drInk)
            VStack(alignment: .leading, spacing: 14) {
                row("tag.fill", "New deals each evening", "Around 7pm we tell you how many new deals there are, and the biggest discount.")
                row("magnifyingglass", "Deals for your words", "Sign in and add words like PS5 or Garmin: we tell you when new deals match them.")
                row("target", "Price alerts", "Watch a product and set a target. We tell you as soon as it gets there.")
            }
            Text("No more than one notification per price check, and none at night. You can change this any time in Settings.")
                .font(.footnote).foregroundStyle(Color.drMuted)
            Spacer()
            Button { Task { await push.requestPermission() } } label: {
                Text("Turn on notifications").font(.headline).frame(maxWidth: .infinity, minHeight: 50)
            }
            .buttonStyle(.borderedProminent)
            Button("Not now") { push.notNow() }.frame(maxWidth: .infinity).padding(.bottom, 10)
        }
        .padding(24)
        .interactiveDismissDisabled()
    }

    private func row(_ icon: String, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: icon).font(.title3).foregroundStyle(Color.drGreen).frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                Text(text).font(.subheadline).foregroundStyle(Color.drMuted)
            }
        }
    }
}

/// Notification switches for Settings. Signed out, only the daily summary applies.
struct NotificationsSection: View {
    @ObservedObject var push = PushManager.shared
    let signedIn: Bool

    var body: some View {
        Section {
            if !push.enabled {
                VStack(alignment: .leading, spacing: 6) {
                    Text(push.status == .denied ? "Notifications are off for Deal Radar." : "Notifications aren't on yet.")
                        .font(.subheadline.weight(.semibold))
                    if push.status == .denied {
                        Button("Open iPhone Settings") {
                            if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }
                        }
                    } else {
                        Button("Turn on notifications") { Task { await push.requestPermission() } }
                    }
                }
            }
            ForEach(signedIn ? PushManager.Pref.allCases : [.daily], id: \.self) { p in
                Toggle(isOn: Binding(get: { push.prefs[p] ?? true }, set: { push.set(p, $0) })) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(p.title)
                        Text(p.note).font(.caption).foregroundStyle(Color.drMuted)
                    }
                }
                .tint(.drGreen)
                .disabled(!push.enabled)
            }
        } header: {
            Text("Notifications")
        } footer: {
            Text("At most one notification per price check, none between 10pm and 7am, and only when there's something new.")
        }
        .task { await push.refreshStatus() }
    }
}
