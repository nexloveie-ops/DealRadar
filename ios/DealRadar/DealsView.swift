import SwiftUI

struct DealsView: View {
    @StateObject private var store = DealsStore()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header
                    DealControls(store: store)
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
