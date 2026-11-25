import Foundation

// Shapes of the dealradar.ie API (snake_case keys are converted by the decoder).

struct Deal: Codable, Identifiable, Hashable {
    let id: String
    let store: String?
    let provider: String?
    let title: String?
    let module: String?
    let category: String?
    let priceMinor: Int?
    let referenceMinor: Int?
    let discountPercent: Double?
    let imageUrl: String?
    let buyUrl: String?
    let affiliate: Bool?
    let priceSource: String?
    let sourceSite: String?
    let checkedAt: String?
    let conditionCode: String?
    let minTopUpMinor: Int?
    let endsOn: String?
    let referenceKind: String?
    let referenceSeenOn: String?
    let summaryEn: String?
    let isPrivate: Bool?
    let why: [Reason]?

    enum CodingKeys: String, CodingKey {
        case id, store, provider, title, module, category, priceMinor, referenceMinor, discountPercent, imageUrl, buyUrl,
             affiliate, priceSource, sourceSite, checkedAt, conditionCode, minTopUpMinor, endsOn, referenceKind,
             referenceSeenOn, summaryEn, why
        case isPrivate = "private"
    }

    var shop: String { store ?? provider ?? "the shop" }
    var name: String { title ?? "" }
    var saving: Int? {
        guard let p = priceMinor, let r = referenceMinor else { return nil }
        return r - p
    }
    var shopShort: String { shop.replacingOccurrences(of: ".ie", with: "") }
}

struct Reason: Codable, Hashable {
    let type: String
    let label: String?
    let isPrivate: Bool?
    enum CodingKeys: String, CodingKey {
        case type, label
        case isPrivate = "private"
    }
}

struct DealsResponse: Codable {
    let generatedAt: String?
    let deals: [Deal]
}

struct Channel: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let group: String
}

struct Tier: Codable, Hashable {
    let name: String
    let searchWords: Int
    let wordDays: Int
    let watchItems: Int
    let watchDays: Int
    let instantChecks: Int?
}

struct Keyword: Codable, Identifiable, Hashable {
    let id: String
    let query: String
    let minDiscount: Int?
    let status: String          // searching / matching / expired
    let expiresAt: String?
    let isPrivate: Bool?
    enum CodingKeys: String, CodingKey {
        case id, query, minDiscount, status, expiresAt
        case isPrivate = "private"
    }
}

struct Condition: Codable, Hashable {
    var type: String            // any / pct / price
    var value: Double?
}

struct Watched: Codable, Identifiable, Hashable {
    let id: String
    let site: String
    let key: String
    let url: String
    let label: String?
    let status: String          // checking / not_checked / expired
    let expiresAt: String?
    let condition: Condition?
}

struct Member: Codable {
    let email: String?
    let channels: [String]
    let keywords: [Keyword]
    let watch: [Watched]
    let tier: Tier
}

struct Collection: Codable {
    let last: String?
    let next: String?
    let automatic: Bool?
}

struct MeResponse: Codable {
    let member: Member
    let channels: [Channel]
    let collection: Collection?
}

struct MemberResponse: Codable {
    let member: Member
}

struct ChannelsResponse: Codable {
    let channels: [String]
}

struct KeywordResult: Codable, Identifiable {
    let id: String
    let query: String
    let count: Int
    let found: Int?
    let searchedAt: String?
    let deals: [Deal]
}

struct KeywordResultsResponse: Codable {
    let keywords: [KeywordResult]
    let collection: Collection?
}

struct Check: Codable, Hashable {
    let status: String?
    let priceMinor: Int?
    let referenceMinor: Int?
    let discountPercent: Double?
    let checkedAt: String?
    let title: String?
    let imageUrl: String?
    let note: String?
    let error: String?
}

struct WatchResult: Codable, Identifiable {
    let id: String
    let met: Bool?
    let check: Check?
}

struct WatchResultsResponse: Codable {
    let watch: [WatchResult]
    let collection: Collection?
}

struct MyDealsResponse: Codable {
    let deals: [Deal]
}

struct APIErrorBody: Codable {
    let error: String?
}
