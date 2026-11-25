import Foundation

/// The public deal list with the same tabs, categories and filters as the homepage.
@MainActor
final class DealsStore: ObservableObject {
    enum Kind: String, CaseIterable, Identifiable {
        case all, amazon, operatorKind = "operator", retailer, mobile123
        var id: String { rawValue }
        var label: String {
            switch self {
            case .all: "All"
            case .amazon: "Amazon"
            case .operatorKind: "Networks"
            case .retailer: "Shops"
            case .mobile123: "Mobile123"
            }
        }
        var menuTitle: String? {
            switch self {
            case .operatorKind: "All network providers"
            case .retailer: "All shops"
            default: nil
            }
        }
    }

    enum Sort: String, CaseIterable, Identifiable {
        case pct, eur, low
        var id: String { rawValue }
        var label: String {
            switch self {
            case .pct: "Biggest discount"
            case .eur: "Biggest saving in €"
            case .low: "Lowest price"
            }
        }
    }

    @Published var deals: [Deal] = []
    /// My deals: the member chose what to follow, so Mobile123 is part of All, and watched products that hit their target come first.
    let mine: Bool

    init(mine: Bool = false) { self.mine = mine }
    @Published var generatedAt: String?
    @Published var loading = false
    @Published var error: String?

    @Published var kind: Kind = .all
    @Published var shop: String?
    @Published var category = "All"
    @Published var minDiscount = 0
    @Published var maxPrice: Int?          // euro
    @Published var sort: Sort = .pct

    static let categoryOrder = ["Phones", "Audio", "Computers", "Tablets", "Watches", "Cameras", "TV & smart home", "Gaming",
                                "Accessories", "Toys & games", "Other"]
    private static let categoryMap: [String: String] = [
        "phones": "Phones", "handset_discount": "Phones", "bonus_credit": "Phones", "headphones": "Audio", "speakers": "Audio",
        "computers": "Computers", "monitors": "Computers", "storage": "Computers", "networking": "Computers", "accessories": "Accessories",
        "tablets": "Tablets", "wearables": "Watches", "cameras": "Cameras", "gaming": "Gaming", "toys": "Toys & games",
        "fire-tv": "TV & smart home", "amazon-devices": "TV & smart home", "smart-home-expanded": "TV & smart home",
        "jbl": "Audio", "beats": "Audio", "sony": "Audio", "anker": "Audio", "audio-expanded": "Audio", "speakers-expanded": "Audio",
        "logitech": "Computers", "peripherals-expanded": "Computers", "hard-drive": "Computers", "nvme": "Computers",
        "storage-expanded": "Computers", "computers-expanded": "Computers", "displays-expanded": "Computers",
        "network-expanded": "Computers", "charging-expanded": "Accessories", "cameras-expanded": "Cameras",
        "wearables-expanded": "Watches", "brands-expanded": "Other",
    ]

    static func categoryOf(_ d: Deal) -> String {
        let key = (d.category ?? "").split(separator: ",").first.map(String.init) ?? ""
        return categoryMap[key] ?? "Other"
    }

    func load() async {
        loading = true
        defer { loading = false }
        do {
            let r: DealsResponse = try await API.get("api/deals")
            deals = Self.dedupe(r.deals)
            generatedAt = r.generatedAt
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// The same listing collected twice (same shop, price and picture or name) is shown once.
    static func dedupe(_ list: [Deal]) -> [Deal] {
        var seen = Set<String>(), out: [Deal] = []
        for d in list {
            let key = "\(d.shop)|\(d.priceMinor ?? -1)|\(d.imageUrl ?? d.name.lowercased())"
            if seen.insert(key).inserted { out.append(d) }
        }
        return out
    }

    // MARK: filtering, as on the homepage

    private func inKind(_ d: Deal) -> Bool {
        switch kind {
        case .all: return mine || d.module != "mobile123"   // on the public list Mobile123 only appears under Mobile123
        default: return d.tab == kind.rawValue
        }
    }

    func matches(_ d: Deal, ignoreCategory: Bool = false) -> Bool {
        guard inKind(d) else { return false }
        if let shop, d.shop != shop { return false }
        if !ignoreCategory, category != "All", Self.categoryOf(d) != category { return false }
        if let maxPrice, (d.priceMinor ?? Int.max) > maxPrice * 100 { return false }
        guard let pct = d.discountPercent else { return false }
        return pct >= Double(minDiscount)
    }

    var visible: [Deal] {
        deals.filter { matches($0) }.sorted { a, b in
            if mine, a.targetReached != b.targetReached { return a.targetReached }
            switch sort {
            case .pct: return (a.discountPercent ?? -1) > (b.discountPercent ?? -1)
            case .eur: return (a.saving ?? -1) > (b.saving ?? -1)
            case .low: return (a.priceMinor ?? .max) < (b.priceMinor ?? .max)
            }
        }
    }

    /// Categories with deals under the current tab, with counts.
    var categories: [(String, Int)] {
        let pool = deals.filter { matches($0, ignoreCategory: true) }
        var counts: [String: Int] = [:]
        pool.forEach { counts[Self.categoryOf($0), default: 0] += 1 }
        return [("All", pool.count)] + Self.categoryOrder.compactMap { c in counts[c].map { (c, $0) } }
    }

    /// Networks or shops in a tab, with how many discounted deals each has.
    func shops(for k: Kind) -> [(String, Int)] {
        var counts: [String: Int] = [:]
        deals.filter { $0.tab == k.rawValue && $0.discountPercent != nil }.forEach { counts[$0.shop, default: 0] += 1 }
        return counts.sorted { $0.key.localizedCompare($1.key) == .orderedAscending }
    }

    var activeFilters: Int { (minDiscount > 0 ? 1 : 0) + (maxPrice != nil ? 1 : 0) }

    func choose(_ k: Kind, shop: String? = nil) {
        kind = k
        self.shop = shop
        if !categories.contains(where: { $0.0 == category }) { category = "All" }
    }
}
