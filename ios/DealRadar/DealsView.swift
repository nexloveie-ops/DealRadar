import SwiftUI

struct DealsView: View {
    @StateObject private var store = DealsStore()
    @State private var showFilters = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header
                    tabs
                    categories
                    content
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .background(Color.drShelf)
            .refreshable { await store.load() }
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Image("Logo").resizable().scaledToFit().frame(height: 30).accessibilityLabel("Deal Radar Ireland")
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.white, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .sheet(isPresented: $showFilters) { FiltersSheet(store: store).presentationDetents([.medium]) }
            .task { if store.deals.isEmpty { await store.load() } }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Phone and tech deals in Ireland")
                .font(.title2.weight(.bold))
                .foregroundStyle(Color.drInk)
                .minimumScaleFactor(0.8)
                .lineLimit(1)
            if let g = store.generatedAt {
                Text("Prices checked \(Format.when(g)) · \(store.visible.count) deals")
                    .font(.footnote).foregroundStyle(Color.drMuted)
            }
        }
        .padding(.top, 8)
    }

    // All · Amazon · Networks ▾ · Shops ▾ · Mobile123, then Filters and sort.
    private var tabs: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 2) {
                ForEach(DealsStore.Kind.allCases) { k in
                    if let all = k.menuTitle {
                        Menu {
                            Button { store.choose(k) } label: { Text("\(all) (\(store.shops(for: k).reduce(0) { $0 + $1.1 }))") }
                            Divider()
                            ForEach(store.shops(for: k), id: \.0) { s in
                                Button { store.choose(k, shop: s.0) } label: {
                                    if store.kind == k && store.shop == s.0 { Label("\(s.0) (\(s.1))", systemImage: "checkmark") }
                                    else { Text("\(s.0) (\(s.1))") }
                                }
                            }
                        } label: {
                            tabLabel(store.kind == k && store.shop != nil ? store.shop! : k.label, on: store.kind == k, menu: true)
                        }
                    } else {
                        Button { store.choose(k) } label: { tabLabel(k.label, on: store.kind == k, menu: false) }
                    }
                }
            }
            .padding(3)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.drLine))

            HStack {
                Button { showFilters = true } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "line.3.horizontal.decrease")
                        Text("Filters")
                        if store.activeFilters > 0 {
                            Text("\(store.activeFilters)").font(.caption.bold()).foregroundStyle(.white)
                                .frame(minWidth: 18, minHeight: 18).background(Color.drBlue, in: Capsule())
                        }
                    }
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Color.white, in: Capsule())
                    .overlay(Capsule().stroke(Color.drLine))
                }
                .foregroundStyle(Color.drInk)
                Spacer()
                Menu {
                    Picker("Sort by", selection: $store.sort) {
                        ForEach(DealsStore.Sort.allCases) { Text($0.label).tag($0) }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(store.sort.label)
                        Image(systemName: "chevron.down").font(.caption.weight(.semibold))
                    }
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(Color.white, in: Capsule())
                    .overlay(Capsule().stroke(Color.drLine))
                }
                .foregroundStyle(Color.drInk)
            }
        }
    }

    private func tabLabel(_ text: String, on: Bool, menu: Bool) -> some View {
        HStack(spacing: 3) {
            Text(text).lineLimit(1).minimumScaleFactor(0.8).truncationMode(.tail)
            if menu { Image(systemName: "chevron.down").font(.system(size: 8, weight: .bold)) }
        }
        .font(.system(size: 14, weight: .medium))
        .foregroundStyle(on ? .white : Color.drMuted)
        .padding(.vertical, 7)
        .padding(.horizontal, 3)
        .frame(maxWidth: .infinity)
        .background(on ? Color.drBlue : .clear, in: RoundedRectangle(cornerRadius: 6))
    }

    private var categories: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(store.categories, id: \.0) { c in
                    let on = store.category == c.0
                    Button { store.category = c.0 } label: {
                        HStack(spacing: 4) {
                            Text(c.0)
                            Text("\(c.1)").foregroundStyle(on ? Color(hex: 0xC9CFDA) : Color.drStrike)
                        }
                        .font(.subheadline)
                        .foregroundStyle(on ? .white : Color.drInk)
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .background(on ? Color.drInk : .white, in: Capsule())
                        .overlay(Capsule().stroke(on ? Color.drInk : Color.drLine))
                    }
                }
            }
        }
    }

    @ViewBuilder private var content: some View {
        if store.deals.isEmpty && store.loading {
            ProgressView("Loading deals…").frame(maxWidth: .infinity).padding(.top, 60)
        } else if let e = store.error, store.deals.isEmpty {
            VStack(spacing: 10) {
                Text("Couldn't load the deals").font(.headline)
                Text(e).font(.footnote).foregroundStyle(Color.drMuted).multilineTextAlignment(.center)
                Button("Try again") { Task { await store.load() } }.buttonStyle(.borderedProminent)
            }
            .frame(maxWidth: .infinity).padding(.top, 60)
        } else if store.visible.isEmpty {
            VStack(spacing: 6) {
                Text("No deals match these filters").font(.headline)
                Text("Try a higher price limit, a smaller discount or another category.")
                    .font(.footnote).foregroundStyle(Color.drMuted).multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity).padding(.top, 50)
        } else {
            DealGrid(deals: store.visible)
        }
    }
}

struct FiltersSheet: View {
    @ObservedObject var store: DealsStore
    @Environment(\.dismiss) private var dismiss
    private let prices: [Int?] = [nil, 50, 100, 250, 500, 1000]

    var body: some View {
        NavigationStack {
            Form {
                Section("Discount") {
                    Picker("Discount", selection: $store.minDiscount) {
                        Text("Any").tag(0)
                        ForEach([20, 30, 40, 50], id: \.self) { Text("\($0)%+").tag($0) }
                    }
                    .pickerStyle(.segmented)
                }
                Section("Price") {
                    Picker("Up to", selection: $store.maxPrice) {
                        ForEach(prices, id: \.self) { p in Text(p.map { "Up to €\($0)" } ?? "Any price").tag(p) }
                    }
                }
                if store.activeFilters > 0 {
                    Button("Clear filters", role: .destructive) { store.minDiscount = 0; store.maxPrice = nil }
                }
            }
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Show \(store.visible.count) deals") { dismiss() } } }
        }
    }
}
