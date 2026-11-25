import SwiftUI

/// The app's version of dealradar.ie/account: shops to follow, search words and watched products.
struct SettingsView: View {
    @EnvironmentObject var auth: AuthManager
    @EnvironmentObject var member: MemberStore

    var body: some View {
        NavigationStack {
            Group {
                if !auth.signedIn {
                    SignInView(title: "Sign in", message: "Sign in to choose shops and networks, add search words and watch products. Everything that matches lands in My deals.")
                } else if let m = member.member {
                    List {
                        nextCheckSection
                        shopsSection(m)
                        WordsSection(m: m)
                        WatchSection(m: m)
                        Section {
                            if let e = m.email { LabeledContent("Signed in as", value: e) }
                            LabeledContent("Plan", value: m.tier.name)
                            Button("Sign out", role: .destructive) { auth.signOut() }
                        }
                    }
                    .refreshable { await member.load() }
                    .navigationDestination(for: String.self) { id in WordDealsView(id: id) }
                } else if member.loading {
                    ProgressView("Loading your settings…")
                } else {
                    VStack(spacing: 10) {
                        Text(member.error ?? "Couldn't load your settings.").multilineTextAlignment(.center)
                        Button("Try again") { Task { await member.load() } }.buttonStyle(.borderedProminent)
                        Button("Sign out", role: .destructive) { auth.signOut() }
                    }
                    .padding()
                }
            }
            .navigationTitle("Settings")
            .alert("Something went wrong", isPresented: Binding(get: { member.error != nil && member.member != nil }, set: { if !$0 { member.error = nil } })) {
                Button("OK", role: .cancel) {}
            } message: { Text(member.error ?? "") }
        }
    }

    private var nextCheckSection: some View {
        Section {
            HStack(spacing: 12) {
                Image(systemName: "dot.radiowaves.left.and.right").font(.title2).foregroundStyle(Color.drGreen)
                VStack(alignment: .leading, spacing: 2) {
                    Text("We check prices twice a day").font(.subheadline.weight(.semibold))
                    if let n = member.collection?.next, let left = Format.span(until: n) {
                        Text("Next check \(Format.when(n, withTime: true)), in \(left).").font(.footnote).foregroundStyle(Color.drMuted)
                    } else if member.collection?.automatic == false {
                        Text("Automatic checks are paused for now.").font(.footnote).foregroundStyle(Color.drMuted)
                    }
                }
            }
        } footer: {
            Text("Everything that matches what you follow lands in My deals. Watched products that hit your target go to the top.")
        }
    }

    private func shopsSection(_ m: Member) -> some View {
        Section {
            ForEach(member.channels) { c in
                Toggle(isOn: Binding(get: { m.channels.contains(c.id) }, set: { on in Task { await member.setChannel(c.id, on: on) } })) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(c.name)
                        Text(c.group).font(.caption).foregroundStyle(Color.drMuted)
                    }
                }
                .tint(.drGreen)
            }
        } header: {
            Text("Shops and networks · following \(m.channels.count) of \(member.channels.count)")
        } footer: {
            Text("Deals from the ones you follow show in My deals. Changes save straight away.")
        }
    }
}

/// Places in use, shown as dots: ●●○ 2/3
struct Slots: View {
    let used: Int
    let total: Int
    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<max(total, 0), id: \.self) { i in
                Circle().strokeBorder(Color.drGreen, lineWidth: 2)
                    .background(Circle().fill(i < used ? Color.drGreen : .clear))
                    .frame(width: 11, height: 11)
            }
            Text("\(used)/\(total) in use").font(.caption).foregroundStyle(Color.drMuted)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(used) of \(total) places in use")
    }
}

struct TimeLeft: View {
    let expiresAt: String?
    let expired: Bool
    var body: some View {
        if expired {
            Text("Expired \(Format.when(expiresAt))").font(.caption).foregroundStyle(Color.drMuted)
        } else if let left = Format.span(until: expiresAt) {
            let low = (Format.date(expiresAt)?.timeIntervalSinceNow ?? 0) < 86_400
            Label("\(left) left", systemImage: "clock").font(.caption).foregroundStyle(low ? Color.drOrange : Color.drMuted)
        }
    }
}

// MARK: - Search words

struct WordsSection: View {
    @EnvironmentObject var member: MemberStore
    let m: Member
    @State private var newWord = ""
    @State private var newMin = 20
    static let mins = [0, 10, 20, 30, 40, 50, 60, 70]

    var body: some View {
        Section {
            HStack {
                TextField("Add a word, e.g. PS5, Garmin", text: $newWord)
                    .textInputAutocapitalization(.never)
                    .submitLabel(.done)
                    .onSubmit(add)
                Picker("", selection: $newMin) { ForEach(Self.mins, id: \.self) { Text(Self.minText($0)).tag($0) } }
                    .labelsHidden().fixedSize()
                Button("Add", action: add).disabled(newWord.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let n = member.notice { Text(n).font(.footnote).foregroundStyle(Color.drGreen) }
            if m.keywords.isEmpty {
                Text("No words yet. Try a brand or a product, like PS5 or Garmin.").font(.footnote).foregroundStyle(Color.drMuted)
            }
            ForEach(m.keywords) { k in WordRow(k: k, result: member.wordResults[k.id], days: m.tier.wordDays) }
        } header: {
            HStack {
                Text("Search words")
                Spacer()
                Slots(used: m.keywords.filter { $0.status == "searching" }.count, total: m.tier.searchWords).textCase(nil)
            }
        } footer: {
            Text("\(m.tier.name) plan: \(m.tier.searchWords) words searched for you at a time, each for \(m.tier.wordDays) days. Swipe left on a word to remove it.")
        }
    }

    static func minText(_ v: Int) -> String { v == 0 ? "Any discount" : "\(v)%+ off" }

    private func add() {
        let q = newWord.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        newWord = ""
        Task { await member.addWord(q, min: newMin) }
    }
}

struct WordRow: View {
    @EnvironmentObject var member: MemberStore
    let k: Keyword
    let result: KeywordResult?
    let days: Int

    var body: some View {
        let expired = k.status == "expired", waiting = k.status == "matching"
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(k.query).font(.body.weight(.semibold)).foregroundStyle(expired ? Color.drStrike : Color.drInk)
                if expired { Pill(text: "Expired") }
                else if waiting { Pill(text: "Waiting for a place", fg: .drOrange, bg: .drOrangeBg) }
                else { Pill(text: "Searching", fg: .drGreen, bg: .drGreenBg) }
                Spacer()
                if !expired, let r = result {
                    NavigationLink(value: r.id) { EmptyView() }.opacity(0).frame(width: 0)
                    Text("\(r.count) deal\(r.count == 1 ? "" : "s")").font(.subheadline.weight(.semibold))
                        .foregroundStyle(r.count > 0 ? Color.drGreen : Color.drMuted)
                }
            }
            HStack(spacing: 12) {
                if !expired {
                    Menu {
                        Picker("Minimum discount", selection: Binding(get: { k.minDiscount ?? 20 }, set: { v in Task { await member.setMin(k.id, v) } })) {
                            ForEach(WordsSection.mins, id: \.self) { Text(WordsSection.minText($0)).tag($0) }
                        }
                    } label: {
                        HStack(spacing: 3) { Text(WordsSection.minText(k.minDiscount ?? 20)); Image(systemName: "chevron.up.chevron.down").font(.caption2) }
                            .font(.caption.weight(.medium))
                    }
                }
                TimeLeft(expiresAt: k.expiresAt, expired: expired)
                Spacer()
                if expired { Button("Renew") { Task { await member.renewWord(k.id) } }.buttonStyle(.borderless).font(.caption.weight(.bold)) }
                else if waiting { Button("Search this") { Task { await member.promoteWord(k.id) } }.buttonStyle(.borderless).font(.caption.weight(.bold)) }
            }
        }
        .padding(.vertical, 2)
        .swipeActions { Button("Remove", role: .destructive) { Task { await member.removeWord(k.id) } } }
    }
}

struct WordDealsView: View {
    @EnvironmentObject var member: MemberStore
    let id: String
    var body: some View {
        let r = member.wordResults[id]
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let r, r.deals.isEmpty {
                    Text("No discounted products for this word right now. We'll keep looking at every check.").foregroundStyle(Color.drMuted)
                }
                if let r, let found = r.found, found > r.count {
                    Text("\(found - r.count) more have a smaller discount; lower the minimum to see them.").font(.footnote).foregroundStyle(Color.drMuted)
                }
                DealGrid(deals: r?.deals ?? [])
            }
            .padding(16)
        }
        .background(Color.drShelf)
        .navigationTitle("“\(r?.query ?? "")”")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Watched products

struct WatchSection: View {
    @EnvironmentObject var member: MemberStore
    let m: Member
    @State private var adding = false

    var body: some View {
        Section {
            Button { adding = true } label: { Label("Watch a product", systemImage: "plus.circle.fill") }
            if m.watch.isEmpty {
                Text("Copy a product link from Amazon.ie, Harvey Norman, Expert, Powercity or Mobile123 and add it here.")
                    .font(.footnote).foregroundStyle(Color.drMuted)
            }
            ForEach(m.watch) { w in WatchRow(w: w, result: member.watchResults[w.id]) }
        } header: {
            HStack {
                Text("Products I'm watching")
                Spacer()
                Slots(used: m.watch.filter { $0.status == "checking" }.count, total: m.tier.watchItems).textCase(nil)
            }
        } footer: {
            Text("\(m.tier.name) plan: \(m.tier.watchItems) products checked at every collection, each for \(m.tier.watchDays) days.")
        }
        .sheet(isPresented: $adding) { AddWatchSheet() }
    }
}

struct WatchRow: View {
    @EnvironmentObject var member: MemberStore
    let w: Watched
    let result: WatchResult?
    @State private var editing = false

    var body: some View {
        let ch = result?.check, expired = w.status == "expired", waiting = w.status == "not_checked"
        let name = ch?.title ?? w.label ?? "\(w.site) product \(w.key)"
        HStack(alignment: .top, spacing: 12) {
            Group {
                if let s = ch?.imageUrl, let u = URL(string: s) {
                    AsyncImage(url: u) { $0.resizable().scaledToFit() } placeholder: { Color.drShelf }
                } else { Color.drShelf }
            }
            .frame(width: 54, height: 54).background(Color.white).clipShape(RoundedRectangle(cornerRadius: 6))

            VStack(alignment: .leading, spacing: 5) {
                Text(name).font(.subheadline.weight(.semibold)).lineLimit(2).foregroundStyle(expired ? Color.drStrike : Color.drInk)
                HStack(spacing: 6) {
                    Text(w.site).font(.caption).foregroundStyle(Color.drBlue)
                    if expired { Pill(text: "Expired") }
                    else if waiting { Pill(text: "Waiting for a place", fg: .drOrange, bg: .drOrangeBg) }
                    else { Pill(text: "Checking", fg: .drGreen, bg: .drGreenBg) }
                }
                if !expired, let p = ch?.priceMinor {
                    HStack(spacing: 6) {
                        PriceTag(minor: p, size: 17)
                        if let r = ch?.referenceMinor { Text(Format.eur(r)).font(.caption).strikethrough().foregroundStyle(Color.drStrike) }
                        if let pct = ch?.discountPercent { Text("\(Int(pct.rounded()))% off").font(.caption.weight(.bold)) }
                        if result?.met == true { Pill(text: "Target reached", fg: .drInk, bg: .drTag) }
                    }
                }
                HStack(spacing: 10) {
                    if !expired {
                        Button { editing = true } label: { Text("Show at: \(conditionText)").font(.caption.weight(.medium)) }
                            .buttonStyle(.borderless)
                    }
                    TimeLeft(expiresAt: w.expiresAt, expired: expired)
                }
                Text(checkText).font(.caption2).foregroundStyle(Color.drMuted)
                if expired { Button("Renew") { Task { await member.renewWatch(w.id) } }.buttonStyle(.borderedProminent).controlSize(.small) }
                else if waiting { Button("Check this") { Task { await member.promoteWatch(w.id) } }.buttonStyle(.bordered).controlSize(.small) }
            }
        }
        .padding(.vertical, 2)
        .swipeActions { Button("Stop watching", role: .destructive) { Task { await member.removeWatch(w.id) } } }
        .sheet(isPresented: $editing) { ConditionSheet(watched: w) }
    }

    private var conditionText: String {
        guard let c = w.condition, c.type != "any", let v = c.value else { return "any discount" }
        return c.type == "pct" ? "\(Int(v))%+ off" : "below \(Format.eur(Int((v * 100).rounded())))"
    }

    private var checkText: String {
        let ch = result?.check
        if w.status == "expired" { return "Not checked while expired." }
        if w.status == "not_checked" { return "Not checked while it waits for a place." }
        guard let ch else { return "Waiting for the first check." }
        switch ch.status {
        case "pending", "not_checked", "error":
            return (ch.note ?? ch.error ?? "No price yet") + " We'll try again at the next check."
        case "unavailable": return "Out of stock. Checked \(Format.when(ch.checkedAt))."
        case "not_found": return "No longer listed by the shop."
        default: return "Price checked \(Format.when(ch.checkedAt))."
        }
    }
}

struct ConditionPicker: View {
    @Binding var condition: Condition
    var body: some View {
        Picker("Show it in My deals at", selection: $condition.type) {
            Text("Any discount").tag("any")
            Text("A discount of at least").tag("pct")
            Text("A price below").tag("price")
        }
        if condition.type != "any" {
            HStack {
                Text(condition.type == "pct" ? "Discount (%)" : "Price (€)")
                TextField(condition.type == "pct" ? "30" : "100", value: $condition.value, format: .number)
                    .keyboardType(.decimalPad).multilineTextAlignment(.trailing)
            }
        }
    }
}

struct AddWatchSheet: View {
    @EnvironmentObject var member: MemberStore
    @Environment(\.dismiss) private var dismiss
    @State private var url = ""
    @State private var label = ""
    @State private var condition = Condition(type: "any", value: nil)
    @State private var saving = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Paste a product link", text: $url).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    if UIPasteboard.general.hasURLs || UIPasteboard.general.hasStrings {
                        Button("Paste") { url = UIPasteboard.general.url?.absoluteString ?? UIPasteboard.general.string ?? url }
                    }
                } footer: { Text("Amazon.ie, Harvey Norman, Expert, Powercity or Mobile123. We check the price straight away.") }
                Section("When to show it") { ConditionPicker(condition: $condition) }
                Section { TextField("Name (optional)", text: $label) }
                if let e = member.error { Text(e).foregroundStyle(Color(hex: 0xA32D2D)) }
            }
            .navigationTitle("Watch a product")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if saving { ProgressView() } else {
                        Button("Watch") {
                            saving = true
                            Task {
                                member.error = nil
                                let ok = await member.addWatch(url: url.trimmingCharacters(in: .whitespaces), label: label, condition: condition)
                                saving = false
                                if ok { dismiss() }
                            }
                        }
                        .disabled(url.trimmingCharacters(in: .whitespaces).isEmpty || (condition.type != "any" && condition.value == nil))
                    }
                }
            }
        }
    }
}

struct ConditionSheet: View {
    @EnvironmentObject var member: MemberStore
    @Environment(\.dismiss) private var dismiss
    let watched: Watched
    @State private var condition = Condition(type: "any", value: nil)

    var body: some View {
        NavigationStack {
            Form { ConditionPicker(condition: $condition) }
                .navigationTitle("When to show it")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") { Task { await member.setCondition(watched.id, condition); dismiss() } }
                            .disabled(condition.type != "any" && condition.value == nil)
                    }
                }
                .onAppear { condition = watched.condition ?? Condition(type: "any", value: nil) }
        }
        .presentationDetents([.medium])
    }
}
