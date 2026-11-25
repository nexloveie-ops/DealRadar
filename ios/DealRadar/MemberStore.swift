import Foundation

/// The signed-in member's follows, words and watched products, kept in step with dealradar.ie/account.
@MainActor
final class MemberStore: ObservableObject {
    @Published var member: Member?
    @Published var channels: [Channel] = []
    @Published var collection: Collection?
    @Published var wordResults: [String: KeywordResult] = [:]
    @Published var watchResults: [String: WatchResult] = [:]
    @Published var myDeals: [Deal] = []
    @Published var myDealsLoaded = false
    /// Set when follows, words or watched products change, so My deals reloads the next time it is shown.
    @Published var myDealsStale = false
    @Published var loading = false
    @Published var error: String?
    @Published var notice: String?

    func reset() {
        member = nil; channels = []; wordResults = [:]; watchResults = [:]; myDeals = []; myDealsLoaded = false; error = nil; notice = nil
    }

    func load() async {
        loading = true
        defer { loading = false }
        do {
            let r: MeResponse = try await API.get("api/me", auth: true)
            member = r.member; channels = r.channels; collection = r.collection
            error = nil
            async let w: Void = loadWordResults()
            async let p: Void = loadWatchResults()
            _ = await (w, p)
        } catch { self.error = error.localizedDescription }
    }

    func loadWordResults() async {
        guard member?.keywords.isEmpty == false else { wordResults = [:]; return }
        if let r: KeywordResultsResponse = try? await API.get("api/me/keywords/results", auth: true) {
            wordResults = Dictionary(uniqueKeysWithValues: r.keywords.map { ($0.id, $0) })
            if let c = r.collection { collection = c }
        }
    }

    func loadWatchResults() async {
        guard member?.watch.isEmpty == false else { watchResults = [:]; return }
        if let r: WatchResultsResponse = try? await API.get("api/me/watch/results", auth: true) {
            watchResults = Dictionary(uniqueKeysWithValues: r.watch.map { ($0.id, $0) })
            if let c = r.collection { collection = c }
        }
    }

    func loadMyDeals() async {
        do {
            let r: MyDealsResponse = try await API.get("api/me/deals", auth: true)
            myDeals = DealsStore.dedupe(r.deals)
            myDealsLoaded = true
            myDealsStale = false
            error = nil
        } catch { self.error = error.localizedDescription }
    }

    /// Runs a member change; the reply carries the updated member.
    private func update(_ path: String, _ method: String, _ json: [String: Any]? = nil, then: (() async -> Void)? = nil) async {
        do {
            let r: MemberResponse = try await API.call(path, method: method, json: json)
            member = r.member
            error = nil
            myDealsStale = true
            await then?()
        } catch { self.error = error.localizedDescription }
    }

    private func esc(_ id: String) -> String { id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id }

    // MARK: shops and networks

    func setChannel(_ id: String, on: Bool) async {
        guard var list = member?.channels else { return }
        if on { if !list.contains(id) { list.append(id) } } else { list.removeAll { $0 == id } }
        do {
            let r: ChannelsResponse = try await API.call("api/me/channels", method: "PUT", json: ["channels": list])
            if let m = member { member = Member(email: m.email, channels: r.channels, keywords: m.keywords, watch: m.watch, tier: m.tier) }
            myDealsStale = true
        } catch { self.error = error.localizedDescription }
    }

    // MARK: search words

    func addWord(_ q: String, min: Int) async {
        await update("api/me/keywords", "POST", ["query": q, "min_discount": min]) { await self.loadWordResults() }
        if error == nil, let k = member?.keywords.first(where: { $0.query.caseInsensitiveCompare(q) == .orderedSame }) {
            notice = k.status == "searching" ? "Added “\(q)”. We search for it from the next check."
                : "Added “\(q)”. All your places are in use, so it waits; tap Search this to swap it in."
        }
    }
    func setMin(_ id: String, _ min: Int) async { await update("api/me/keywords/\(esc(id))", "PUT", ["min_discount": min]) { await self.loadWordResults() } }
    func renewWord(_ id: String) async { await update("api/me/keywords/\(esc(id))/renew", "POST") { await self.loadWordResults() } }
    func removeWord(_ id: String) async { await update("api/me/keywords/\(esc(id))", "DELETE") }
    func promoteWord(_ id: String) async {
        guard let ids = member?.keywords.map(\.id) else { return }
        await update("api/me/keywords/order", "PUT", ["ids": [id] + ids.filter { $0 != id }]) { await self.loadWordResults() }
    }

    // MARK: watched products

    func addWatch(url: String, label: String, condition: Condition) async -> Bool {
        var cond: [String: Any] = ["type": condition.type]
        cond["value"] = condition.type == "any" ? NSNull() : (condition.value as Any)
        do {
            let r: MemberResponse = try await API.call("api/me/watch", method: "POST", json: ["url": url, "label": label, "condition": cond])
            member = r.member
            error = nil
            myDealsStale = true
            await loadWatchResults()
            notice = member?.watch.last?.status == "checking" ? "Added. We check its price at every collection."
                : "Added. All your places are in use, so it waits; tap Check this to swap it in."
            return true
        } catch { self.error = error.localizedDescription; return false }
    }
    func setCondition(_ id: String, _ c: Condition) async {
        await update("api/me/watch/\(esc(id))", "PUT", ["condition": ["type": c.type, "value": c.type == "any" ? NSNull() : (c.value as Any)]]) {
            await self.loadWatchResults()
        }
    }
    func renewWatch(_ id: String) async { await update("api/me/watch/\(esc(id))/renew", "POST") { await self.loadWatchResults() } }
    func removeWatch(_ id: String) async { await update("api/me/watch/\(esc(id))", "DELETE") }
    func promoteWatch(_ id: String) async {
        guard let ids = member?.watch.map(\.id) else { return }
        await update("api/me/watch/order", "PUT", ["ids": [id] + ids.filter { $0 != id }]) { await self.loadWatchResults() }
    }
}
