/* Ram's Wedding guest-list dashboard.
 * All guest text is inserted with textContent, never innerHTML, so names and
 * messages from the CSV can never be interpreted as markup. */

(function () {
  "use strict";

  var REVIEW_TAB = "__review__";
  var ALL_TAB = "__all__";

  var ATTENDING = "Attending";

  var state = {
    data: null,
    tab: ALL_TAB,
    query: "",
    showContacts: true,
    flaggedNames: new Set(),
    // category filters, only meaningful on the Attending tab
    filter: { category: "", friend_of: "", friend_location: "", family_location: "" },
    // "overview" (drill-down dashboard) or "list", Attending tab only
    view: "overview",
    // which category / sub-group the overview is drilled into
    drill: { category: null, friendOf: null, location: null }
  };

  var els = {
    tabs: document.getElementById("tabs"),
    results: document.getElementById("results"),
    review: document.getElementById("review"),
    toolbar: document.getElementById("toolbar"),
    search: document.getElementById("search"),
    clear: document.getElementById("clear-search"),
    contacts: document.getElementById("show-contacts"),
    count: document.getElementById("result-count"),
    categoryBar: document.getElementById("category-bar"),
    viewSwitch: document.getElementById("view-switch"),
    overview: document.getElementById("overview"),
    modal: document.getElementById("modal"),
    modalTitle: document.getElementById("modal-title"),
    modalBody: document.getElementById("modal-body"),
    modalCancel: document.getElementById("modal-cancel"),
    modalConfirm: document.getElementById("modal-confirm"),
    toast: document.getElementById("toast")
  };

  // ----------------------------------------------------------- helpers
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null && text !== "") node.textContent = text;
    return node;
  }

  function slug(status) {
    return String(status).toLowerCase().replace(/[^a-z0-9]+/g, "-");
  }

  function fact(label, value, wrapable) {
    var span = el("span", wrapable ? "wrapable" : null);
    span.appendChild(document.createTextNode(label + " "));
    span.appendChild(el("strong", null, String(value)));
    return span;
  }

  /* Dialable form of a stored number, or null when it cannot be trusted.
   * One record ("9.19943E+11") was mangled into scientific notation by the
   * source export, so anything carrying a letter is left as plain text
   * rather than turned into a link that would dial the wrong person. */
  function telNumber(raw) {
    if (!raw || /[a-z]/i.test(raw)) return null;
    var digits = raw.replace(/\D/g, "");
    if (digits.length < 7 || digits.length > 15) return null;
    return "+" + digits;
  }

  /* Readable grouping. The untouched original stays in the title attribute. */
  function prettyPhone(raw) {
    var d = raw.replace(/\D/g, "");
    if (d.length === 11 && d.charAt(0) === "1") {
      return "+1 " + d.slice(1, 4) + " " + d.slice(4, 7) + " " + d.slice(7);
    }
    if (d.length === 10) {
      return "(" + d.slice(0, 3) + ") " + d.slice(3, 6) + "-" + d.slice(6);
    }
    return "+" + d;
  }

  function action(className, label, href) {
    var link = el("a", "chip " + className, label);
    link.href = href;
    link.rel = "nofollow";
    return link;
  }

  function phoneFact(raw) {
    var span = el("span", "contact");
    span.appendChild(document.createTextNode("Phone "));

    var tel = telNumber(raw);
    if (!tel) {
      var plain = el("strong", null, raw);
      plain.title = "Stored as: " + raw;
      span.appendChild(plain);
      span.appendChild(el("span", "contact-note", "number incomplete in source"));
      return span;
    }

    var number = el("a", "contact-link", prettyPhone(raw));
    number.href = "tel:" + tel;
    number.title = "Call " + raw;
    span.appendChild(number);
    span.appendChild(action("chip-call", "Call", "tel:" + tel));
    span.appendChild(action("chip-sms", "SMS", "sms:" + tel));
    return span;
  }

  function emailFact(raw) {
    var span = el("span", "contact wrapable");
    span.appendChild(document.createTextNode("Email "));
    var link = el("a", "contact-link", raw);
    link.href = "mailto:" + raw;
    span.appendChild(link);
    span.appendChild(action("chip-mail", "Email", "mailto:" + raw));
    return span;
  }

  /* Names mentioned in the duplicates list, so a party can be badged. */
  function buildFlaggedNames(duplicates) {
    var names = new Set();
    duplicates.forEach(function (dup) {
      [dup.record_a, dup.record_b].forEach(function (record) {
        var name = String(record).split(" - ")[0].trim().toLowerCase();
        if (name) names.add(name);
      });
    });
    return names;
  }

  function select(field, options, current, includeBlank) {
    var sel = el("select");
    sel.setAttribute("data-field", field);
    if (includeBlank) sel.appendChild(el("option", null, includeBlank)).value = "";
    options.forEach(function (opt) {
      var node = el("option", null, opt);
      node.value = opt;
      if (opt === current) node.selected = true;
      sel.appendChild(node);
    });
    if (!current) sel.value = "";
    return sel;
  }

  function toast(message, isError) {
    els.toast.textContent = message;
    els.toast.className = "toast" + (isError ? " toast-error" : "");
    els.toast.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { els.toast.hidden = true; }, 3200);
  }

  function post(path, payload) {
    return fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      if (response.status === 401 || response.redirected) {
        window.location.href = "/login";
        throw new Error("signed out");
      }
      return response.json().then(function (body) {
        if (!response.ok) throw new Error(body.error || ("HTTP " + response.status));
        return body;
      });
    });
  }

  /* Repaint the server-rendered summary cards after a change. */
  function paintSummary(summary) {
    Object.keys(summary).forEach(function (metric) {
      var node = document.querySelector('[data-metric="' + metric + '"]');
      if (node) node.textContent = summary[metric];
    });
    var sub = document.querySelector('[data-sub="attending"]');
    if (sub) {
      sub.textContent = summary.attending_adults + " adults · " +
                        summary.attending_kids + " kids";
    }
    var totalSub = document.querySelector('[data-sub="total"]');
    if (totalSub) {
      totalSub.textContent = summary.confirmed_people + " confirmed · " +
                             summary.estimated_people + " estimated";
    }
  }

  function partyIsFlagged(party) {
    if (state.flaggedNames.has(party.name.trim().toLowerCase())) return true;
    return party.members.some(function (m) {
      return state.flaggedNames.has(m.full_name.trim().toLowerCase());
    });
  }

  // -------------------------------------------------------------- tabs
  function buildTabs() {
    var summary = state.data.summary;
    var tabs = [{ key: ALL_TAB, label: "All", count: summary.total_people }];

    summary.by_status.forEach(function (row) {
      if (row.entries > 0) {
        tabs.push({ key: row.status, label: row.status, count: row.people });
      }
    });
    tabs.push({
      key: REVIEW_TAB,
      label: "Review · Duplicates",
      count: state.data.duplicates.length
    });

    els.tabs.textContent = "";
    tabs.forEach(function (tab) {
      var button = el("button", "tab");
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(tab.key === state.tab));
      button.appendChild(document.createTextNode(tab.label));
      button.appendChild(el("span", "pill", String(tab.count)));
      button.addEventListener("click", function () {
        state.tab = tab.key;
        if (tab.key !== ATTENDING) {
          state.drill = { category: null, friendOf: null, location: null };
        }
        buildTabs();
        renderCategoryBar();
        renderViewSwitch();
        render();
      });
      els.tabs.appendChild(button);
    });
  }

  // ------------------------------------------------------------ members
  function renderMember(member, isActive) {
    var li = el("li", "member" + (isActive ? "" : " other-status"));
    li.appendChild(el("div", "member-role", member.guest_label));

    var body = el("div", "member-body");

    var nameRow = el("p", "member-name");
    var nameNode = el("span", member.name_missing ? "name-missing" : null, member.full_name);
    nameRow.appendChild(nameNode);
    if (!isActive) {
      nameRow.appendChild(el("span", "badge b-" + slug(member.status), member.status));
    }
    if (member.moved) {
      nameRow.appendChild(el("span", "moved-flag",
        "moved from " + member.source_status));
    }
    body.appendChild(nameRow);

    var facts = el("p", "facts");
    facts.appendChild(fact(member.confirmed ? "People (confirmed)" : "People (est.)",
                           member.people_count));
    if (member.status === "Attending") {
      facts.appendChild(fact("Adults", member.adults));
      facts.appendChild(fact("Kids", member.kids));
    } else if (member.invited > 0) {
      facts.appendChild(fact("Invited", member.invited));
    }
    if (state.showContacts && member.phone) facts.appendChild(phoneFact(member.phone));
    if (state.showContacts && member.email) facts.appendChild(emailFact(member.email));
    if (member.channel) facts.appendChild(fact("Channel", member.channel));
    if (member.group_name) facts.appendChild(fact("Group", member.group_name, true));
    if (member.guest_tags) facts.appendChild(fact("Tags", member.guest_tags, true));
    body.appendChild(facts);

    if (member.message) body.appendChild(el("p", "message", member.message));

    li.appendChild(body);
    return li;
  }

  // -------------------------------------------------- category summary bar
  function countChip(label, value, className, filterField, filterValue) {
    var chip = el("button", "count-chip" + (className ? " " + className : ""));
    chip.type = "button";
    chip.appendChild(el("span", "count-chip-label", label));
    chip.appendChild(el("span", "count-chip-value", String(value)));
    if (filterField) {
      var active = state.filter[filterField] === filterValue;
      if (active) chip.classList.add("is-active");
      chip.setAttribute("aria-pressed", String(active));
      chip.addEventListener("click", function () {
        // one filter dimension at a time keeps the counts unambiguous
        state.filter = { category: "", friend_of: "",
                         friend_location: "", family_location: "" };
        if (!active) state.filter[filterField] = filterValue;
        renderCategoryBar();
        render();
      });
    } else {
      chip.disabled = true;
    }
    return chip;
  }

  function renderCategoryBar() {
    if (state.tab !== ATTENDING) {
      els.categoryBar.hidden = true;
      return;
    }
    var s = state.data.summary;
    els.categoryBar.hidden = false;
    els.categoryBar.textContent = "";

    var head = el("div", "cat-head");
    head.appendChild(el("h2", "cat-title", "Attending by category"));
    head.appendChild(el("p", "cat-note",
      "Counts are people, not cards — a party of four adds four."));
    els.categoryBar.appendChild(head);

    var row = el("div", "chip-row");
    row.appendChild(countChip("Attending", s.attending, "chip-total"));
    row.appendChild(countChip("Family", s.attending_by_category.Family,
                              "chip-family", "category", "Family"));
    row.appendChild(countChip("Friends", s.attending_by_category.Friend,
                              "chip-friend", "category", "Friend"));
    row.appendChild(countChip("Musicians", s.attending_by_category.Musician,
                              "chip-musician", "category", "Musician"));
    row.appendChild(countChip("Other", s.attending_by_category.Other,
                              "chip-other", "category", "Other"));
    row.appendChild(countChip("Uncategorised", s.attending_by_category["Uncategorised"],
                              "chip-none", "category", "__none__"));
    els.categoryBar.appendChild(row);

    function group(title, source, field, prefix) {
      var keys = Object.keys(source || {});
      if (!keys.length) return;
      var wrap = el("div", "chip-group");
      wrap.appendChild(el("p", "chip-group-title", title));
      var inner = el("div", "chip-row");
      keys.sort().forEach(function (k) {
        inner.appendChild(countChip((prefix || "") + k, source[k],
                                    "chip-sub", field, k));
      });
      wrap.appendChild(inner);
      els.categoryBar.appendChild(wrap);
    }

    group("Friends by person", s.attending_by_friend_of, "friend_of", "");
    group("Friends by location", s.attending_by_friend_location, "friend_location", "");
    group("Family by location", s.attending_by_family_location, "family_location", "");

    var anyFilter = Object.keys(state.filter).some(function (k) {
      return state.filter[k];
    });
    if (anyFilter) {
      var clear = el("button", "btn btn-ghost btn-small", "Clear category filter");
      clear.type = "button";
      clear.addEventListener("click", function () {
        state.filter = { category: "", friend_of: "",
                         friend_location: "", family_location: "" };
        renderCategoryBar();
        render();
      });
      els.categoryBar.appendChild(clear);
    }
  }

  function partyMatchesFilter(party) {
    var f = state.filter;
    var cat = party.category || {};
    if (f.category === "__none__") return !cat.category;
    if (f.category && cat.category !== f.category) return false;
    if (f.friend_of && cat.friend_of !== f.friend_of) return false;
    if (f.friend_location && cat.friend_location !== f.friend_location) return false;
    if (f.family_location && cat.family_location !== f.family_location) return false;
    return true;
  }

  // ------------------------------------------------- inline category editor
  function categoryEditor(party) {
    var vocab = state.data.vocab;
    var saved = party.category || {};
    var form = el("div", "cat-editor");

    function field(labelText, node, className) {
      var wrap = el("label", "cat-field" + (className ? " " + className : ""));
      wrap.appendChild(el("span", "cat-field-label", labelText));
      wrap.appendChild(node);
      return wrap;
    }

    var catSel = select("category", vocab.categories, saved.category, "Not set");
    var friendOf = select("friend_of", vocab.friend_of, saved.friend_of, "Not set");
    var friendLoc = select("friend_location", vocab.friend_locations,
                           saved.friend_location, "Not set");
    var familyLoc = select("family_location", vocab.family_locations,
                           saved.family_location, "Not set");

    var fCat = field("Category", catSel);
    var fWho = field("Whose friend?", friendOf, "only-friend");
    var fFriendLoc = field("Friend location", friendLoc, "only-friend");
    var fFamilyLoc = field("Family location", familyLoc, "only-family");

    function syncVisibility() {
      var value = catSel.value;
      fWho.hidden = value !== "Friend";
      fFriendLoc.hidden = value !== "Friend";
      fFamilyLoc.hidden = value !== "Family";
    }
    catSel.addEventListener("change", syncVisibility);
    syncVisibility();

    [fCat, fWho, fFriendLoc, fFamilyLoc].forEach(function (n) { form.appendChild(n); });

    var save = el("button", "btn btn-primary btn-small", "Save");
    save.type = "button";
    var note = el("span", "cat-editor-note");
    if (saved.updated_at) note.textContent = "saved";

    save.addEventListener("click", function () {
      save.disabled = true;
      note.textContent = "saving…";
      post("/api/category", {
        party_key: party.party_key,
        category: catSel.value,
        friend_of: friendOf.value,
        friend_location: friendLoc.value,
        family_location: familyLoc.value
      }).then(function (body) {
        state.data.summary = body.summary;
        replaceParty(body.party);
        paintSummary(body.summary);
        renderCategoryBar();
        note.textContent = "saved";
        toast(party.name + " categorised.");
        render();
      }).catch(function (err) {
        note.textContent = "";
        save.disabled = false;
        toast(err.message, true);
      });
    });

    form.appendChild(save);
    form.appendChild(note);
    return form;
  }

  function replaceParty(updated) {
    if (!updated) return;
    for (var i = 0; i < state.data.parties.length; i++) {
      if (state.data.parties[i].party_key === updated.party_key) {
        state.data.parties[i] = updated;
        return;
      }
    }
  }

  // ------------------------------------------------------- move to attending
  function openMoveDialog(party, members) {
    var total = members.reduce(function (n, m) { return n + m.people_count; }, 0);
    els.modalTitle.textContent = "Move to Attending?";
    els.modalConfirm.textContent = "Confirm move";
    els.modalBody.textContent = "";

    var lead = el("p", "modal-lead");
    lead.appendChild(document.createTextNode("Move "));
    lead.appendChild(el("strong", null, party.name));
    lead.appendChild(document.createTextNode(
      " (" + total + (total === 1 ? " guest" : " guests") + ") to Attending?"));
    els.modalBody.appendChild(lead);

    els.modalBody.appendChild(el("p", "modal-note",
      members.length + (members.length === 1 ? " record" : " records") +
      " will change. Everyone else in this party is untouched. " +
      "Confirm how many people are actually coming — the current figure is an " +
      "estimate from the Invited column."));

    var rows = [];
    members.forEach(function (member) {
      var row = el("div", "move-row");
      var head = el("p", "move-row-head");
      head.appendChild(el("strong", null, member.full_name));
      head.appendChild(el("span", "badge b-" + slug(member.status), member.status));
      head.appendChild(el("span", "move-row-label", member.guest_label));
      row.appendChild(head);

      // everything the CSV currently says about this record, before any edit
      var facts = el("dl", "move-facts");
      [["Party", party.name],
       ["Current status", member.status],
       ["Invited", member.invited],
       ["Total Attending", member.source_total_attending],
       ["Adults", member.adults],
       ["Kids", member.kids]].forEach(function (pair) {
        facts.appendChild(el("dt", null, pair[0]));
        facts.appendChild(el("dd", null, String(pair[1])));
      });
      row.appendChild(facts);

      var inputs = el("div", "move-inputs");
      function num(labelText, value, min) {
        var wrap = el("label", "move-num");
        wrap.appendChild(el("span", null, labelText));
        var input = el("input");
        input.type = "number";
        input.min = String(min === undefined ? 0 : min);
        input.value = String(value);
        input.inputMode = "numeric";
        wrap.appendChild(input);
        return { wrap: wrap, input: input };
      }
      var people = num("People", member.people_count, 1);
      var adults = num("Adults", member.people_count);
      var kids = num("Kids", 0);
      // keep adults in step with the headcount unless edited by hand
      people.input.addEventListener("input", function () {
        var t = parseInt(people.input.value, 10) || 0;
        var k = parseInt(kids.input.value, 10) || 0;
        adults.input.value = String(Math.max(t - k, 0));
      });
      kids.input.addEventListener("input", function () {
        var t = parseInt(people.input.value, 10) || 0;
        var k = parseInt(kids.input.value, 10) || 0;
        adults.input.value = String(Math.max(t - k, 0));
      });
      [people, adults, kids].forEach(function (n) { inputs.appendChild(n.wrap); });
      row.appendChild(inputs);
      els.modalBody.appendChild(row);
      rows.push({ member: member, people: people.input,
                  adults: adults.input, kids: kids.input });
    });

    els.modal.hidden = false;
    els.modalConfirm.disabled = false;
    els.modalConfirm.focus();

    els.modalConfirm.onclick = function () {
      var payload = rows.map(function (r) {
        return {
          record_key: r.member.record_key,
          total_attending: parseInt(r.people.value, 10) || 0,
          adults: parseInt(r.adults.value, 10) || 0,
          kids: parseInt(r.kids.value, 10) || 0
        };
      });
      els.modalConfirm.disabled = true;
      post("/api/move", { to_status: ATTENDING, records: payload })
        .then(function (body) {
          closeModal();
          state.data.summary = body.summary;
          replaceParty(body.party);
          paintSummary(body.summary);
          buildTabs();
          renderCategoryBar();
          render();
          toast(party.name + " moved to Attending.");
        })
        .catch(function (err) {
          els.modalConfirm.disabled = false;
          toast(err.message, true);
        });
    };
  }

  function closeModal() {
    els.modal.hidden = true;
    els.modalConfirm.onclick = null;
  }

  els.modalCancel.addEventListener("click", closeModal);
  els.modal.addEventListener("click", function (event) {
    if (event.target === els.modal) closeModal();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !els.modal.hidden) closeModal();
  });

  // ------------------------------------------- attending overview (L1-L3)
  /* The overview answers "how are the 242 distributed?" in one screen. Guest
   * cards only appear once a category is chosen, so nothing here scrolls past
   * a few hundred pixels until you ask it to. */
  var CATEGORY_DEFS = [
    { key: "Family", label: "Family", cls: "is-family",
      subKind: "family_location" },
    { key: "Friend", label: "Friends", cls: "is-friends",
      subKind: "friend_of" },
    { key: "Musician", label: "Musicians", cls: "is-musicians", subKind: null },
    { key: "Other", label: "Other", cls: "is-other", subKind: null },
    { key: "__none__", label: "Uncategorised", cls: "is-none", subKind: null }
  ];

  var UNSPECIFIED = "Unspecified";

  function loadView() {
    try {
      return window.localStorage.getItem("attendingView") === "list"
        ? "list" : "overview";
    } catch (e) {
      return "overview";
    }
  }

  function saveView(view) {
    try {
      window.localStorage.setItem("attendingView", view);
    } catch (e) { /* private mode or storage blocked -- not worth failing over */ }
  }

  function renderViewSwitch() {
    var show = state.tab === ATTENDING;
    els.viewSwitch.hidden = !show;
    if (!show) return;
    [].forEach.call(els.viewSwitch.querySelectorAll(".view-btn"), function (btn) {
      var active = btn.getAttribute("data-view") === state.view;
      btn.setAttribute("aria-pressed", String(active));
      btn.classList.toggle("is-active", active);
    });
  }

  function catKeyOf(party) {
    return (party.category || {}).category || "__none__";
  }

  function statsFor(list) {
    return {
      parties: list.length,
      people: list.reduce(function (n, p) { return n + p.attending_people; }, 0)
    };
  }

  function statLine(stats) {
    return stats.people + (stats.people === 1 ? " person" : " people") + " · " +
           stats.parties + (stats.parties === 1 ? " party" : " parties");
  }

  /* Group a list of parties by one category field, preserving vocab order. */
  function groupBy(list, field, order) {
    var buckets = {};
    list.forEach(function (p) {
      var value = (p.category || {})[field] || UNSPECIFIED;
      (buckets[value] = buckets[value] || []).push(p);
    });
    var keys = (order || []).filter(function (k) { return buckets[k]; });
    Object.keys(buckets).forEach(function (k) {
      if (keys.indexOf(k) === -1) keys.push(k);
    });
    return keys.map(function (k) {
      return { label: k, parties: buckets[k], stats: statsFor(buckets[k]) };
    });
  }

  function attendingPool() {
    var query = state.query.trim().toLowerCase();
    return state.data.parties.filter(function (p) {
      if (p.attending_people <= 0) return false;
      return !query || p.search_blob.indexOf(query) !== -1;
    });
  }

  function drillMatches(party) {
    var cat = party.category || {};
    var d = state.drill;
    if (d.category === "__none__") {
      if (cat.category) return false;
    } else if (d.category && cat.category !== d.category) {
      return false;
    }
    if (d.friendOf && (cat.friend_of || UNSPECIFIED) !== d.friendOf) return false;
    if (d.location) {
      var loc = d.category === "Family" ? cat.family_location : cat.friend_location;
      if ((loc || UNSPECIFIED) !== d.location) return false;
    }
    return true;
  }

  function setDrill(category, friendOf, location) {
    state.drill = { category: category, friendOf: friendOf, location: location };
    render();
  }

  // ------------------------------------------------------------ level 1
  function categoryCard(def, stats, members) {
    var card = el("button", "cat-card " + def.cls);
    card.type = "button";
    if (state.drill.category === def.key) card.classList.add("is-open");
    card.setAttribute("aria-pressed", String(state.drill.category === def.key));

    card.appendChild(el("span", "cat-card-label", def.label));
    card.appendChild(el("span", "cat-card-people", String(stats.people)));
    card.appendChild(el("span", "cat-card-unit",
      stats.people === 1 ? "person" : "people"));
    card.appendChild(el("span", "cat-card-parties",
      stats.parties + (stats.parties === 1 ? " party" : " parties")));

    // the subcategory split, inline, so level 1 alone tells the story
    if (def.subKind && stats.people) {
      var order = def.subKind === "friend_of"
        ? state.data.vocab.friend_of
        : state.data.vocab.family_locations;
      var subs = groupBy(members, def.subKind, order);
      var list = el("span", "cat-card-subs");
      subs.forEach(function (sub) {
        var row = el("span", "cat-sub-row");
        row.appendChild(el("span", "cat-sub-name", sub.label));
        row.appendChild(el("span", "cat-sub-val", String(sub.stats.people)));
        list.appendChild(row);
      });
      card.appendChild(list);
    } else if (stats.people) {
      card.appendChild(el("span", "cat-card-hint", "no sub-groups"));
    }

    card.addEventListener("click", function () {
      setDrill(state.drill.category === def.key ? null : def.key, null, null);
    });
    return card;
  }

  // ------------------------------------------------------------ level 2
  function subTile(label, stats, active, onClick) {
    var tile = el("button", "sub-tile" + (active ? " is-active" : ""));
    tile.type = "button";
    tile.setAttribute("aria-pressed", String(active));
    tile.appendChild(el("span", "sub-tile-name", label));
    tile.appendChild(el("span", "sub-tile-stats", statLine(stats)));
    tile.addEventListener("click", onClick);
    return tile;
  }

  function subSection(title, tiles, cls) {
    var wrap = el("section", "sub-section " + (cls || ""));
    wrap.appendChild(el("h4", "sub-section-title", title));
    var row = el("div", "sub-row");
    tiles.forEach(function (t) { row.appendChild(t); });
    wrap.appendChild(row);
    return wrap;
  }

  // -------------------------------------------------------- breadcrumb
  function breadcrumb() {
    var bar = el("nav", "crumbs");
    bar.setAttribute("aria-label", "Breadcrumb");

    function crumb(label, onClick, isLast) {
      if (isLast) {
        bar.appendChild(el("span", "crumb crumb-current", label));
        return;
      }
      var btn = el("button", "crumb crumb-link", label);
      btn.type = "button";
      btn.addEventListener("click", onClick);
      bar.appendChild(btn);
      bar.appendChild(el("span", "crumb-sep", "›"));
    }

    var d = state.drill;
    var def = CATEGORY_DEFS.filter(function (x) { return x.key === d.category; })[0];
    var last = !d.friendOf && !d.location;

    crumb("Attending", function () { setDrill(null, null, null); }, !d.category);
    if (def) crumb(def.label, function () { setDrill(d.category, null, null); }, last);
    if (d.friendOf) {
      crumb(d.friendOf, function () { setDrill(d.category, d.friendOf, null); },
            !d.location);
    }
    if (d.location) crumb(d.location, null, true);

    var showAll = el("button", "btn btn-ghost btn-small crumbs-all",
                     "Show all attending");
    showAll.type = "button";
    showAll.addEventListener("click", function () {
      state.view = "list";
      saveView("list");
      renderViewSwitch();
      render();
    });
    bar.appendChild(showAll);
    return bar;
  }

  // ---------------------------------------------------- guest card bits
  /* The colour class for a party, so every card, chip and tile that shows a
   * category uses that one category colour -- never a shade per person. */
  function catClassFor(party) {
    var key = (party.category || {}).category || "__none__";
    var def = CATEGORY_DEFS.filter(function (d) { return d.key === key; })[0];
    return def ? def.cls : "is-none";
  }

  /* "Friend · Ram · Outside NC" -- the whole categorisation as one readable
   * label, so a categorised card can be scanned without any controls on it. */
  function categoryText(party) {
    var cat = party.category || {};
    if (!cat.category) return "Uncategorised";
    var parts = [cat.category];
    if (cat.category === "Friend") {
      if (cat.friend_of) parts.push(cat.friend_of);
      if (cat.friend_location) parts.push(cat.friend_location);
    } else if (cat.category === "Family" && cat.family_location) {
      parts.push(cat.family_location);
    }
    return parts.join(" · ");
  }

  function savedLabel(party) {
    var cat = (party.category || {}).category;
    return el("span", "cat-saved" + (cat ? "" : " cat-saved-empty"),
              categoryText(party));
  }

  /* Collapsed by default: the saved label plus a small Edit action. The
   * dropdowns only exist once Edit is pressed, and saving re-renders, which
   * collapses the card again. */
  function categoryControl(party) {
    var hasCategory = !!(party.category || {}).category;
    var wrap = el("div", "cat-control " + catClassFor(party));

    var row = el("div", "cat-control-row");
    row.appendChild(savedLabel(party));

    var toggle = el("button", "btn btn-ghost btn-small",
                    hasCategory ? "Edit" : "Categorise");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    row.appendChild(toggle);
    wrap.appendChild(row);

    var drawer = el("div", "cat-drawer");
    drawer.hidden = true;
    toggle.addEventListener("click", function () {
      drawer.hidden = !drawer.hidden;
      toggle.setAttribute("aria-expanded", String(!drawer.hidden));
      toggle.textContent = drawer.hidden
        ? (hasCategory ? "Edit" : "Categorise")
        : "Cancel";
      if (!drawer.hidden && !drawer.childNodes.length) {
        drawer.appendChild(categoryEditor(party));
      }
    });
    wrap.appendChild(drawer);
    return wrap;
  }

  /* Compact card used at level 3. The category editor lives in a drawer so a
   * party can be re-categorised without leaving the drill-down. */
  function guestCard(party) {
    var card = el("article", "pcard " + catClassFor(party));

    var head = el("div", "pcard-head");
    head.appendChild(el("h4", "pcard-name", party.name));
    head.appendChild(el("span", "pcard-count",
      party.attending_people + (party.attending_people === 1 ? " person" : " people")));
    card.appendChild(head);

    if (party.is_group) {
      card.appendChild(el("p", "pcard-meta", party.member_count + " in party"));
    }

    card.appendChild(categoryControl(party));

    var undo = undoButton(party);
    if (undo) {
      var row = el("div", "pcard-actions");
      row.appendChild(undo);
      card.appendChild(row);
    }
    return card;
  }

  // ------------------------------------------------------------ level 3
  function guestList(parties) {
    var wrap = el("section", "drill-list");
    var stats = statsFor(parties);
    var head = el("div", "drill-head");
    head.appendChild(el("h3", "drill-title", "Guests"));
    head.appendChild(el("p", "drill-stats", statLine(stats)));
    wrap.appendChild(head);

    if (!parties.length) {
      wrap.appendChild(el("p", "bucket-empty", "No attending guests here."));
      return wrap;
    }
    var grid = el("div", "drill-grid");
    parties.forEach(function (p) { grid.appendChild(guestCard(p)); });
    wrap.appendChild(grid);
    return wrap;
  }

  function renderOverview() {
    els.overview.textContent = "";
    var pool = attendingPool();
    var d = state.drill;

    // ---- level 1: always visible, always the whole picture
    var total = statsFor(pool);
    var header = el("div", "ov-head");
    header.appendChild(el("h2", "ov-title", "Attending by category"));
    header.appendChild(el("p", "ov-sub",
      statLine(total) + " · counts are people, not cards. Pick a category to drill in."));
    els.overview.appendChild(header);

    var grid = el("div", "cat-grid");
    CATEGORY_DEFS.forEach(function (def) {
      var members = pool.filter(function (p) { return catKeyOf(p) === def.key; });
      grid.appendChild(categoryCard(def, statsFor(members), members));
    });
    els.overview.appendChild(grid);

    if (!d.category) {
      var hint = el("p", "ov-hint",
        "Choose a category above to see its sub-groups and guests. "
        + "Nothing is loaded below until you do, so this stays a five-second read.");
      els.overview.appendChild(hint);
      els.count.textContent = statLine(total);
      return;
    }

    els.overview.appendChild(breadcrumb());

    // ---- level 2: sub-groups for the chosen category
    var inCategory = pool.filter(function (p) { return catKeyOf(p) === d.category; });

    if (d.category === "Friend") {
      var whoTiles = groupBy(inCategory, "friend_of",
                             state.data.vocab.friend_of).map(function (g) {
        return subTile(g.label === UNSPECIFIED ? "Not said" : g.label + "'s friends",
                       g.stats, d.friendOf === g.label,
                       function () {
                         setDrill(d.category,
                                  d.friendOf === g.label ? null : g.label, null);
                       });
      });
      if (whoTiles.length) els.overview.appendChild(subSection("Whose friend", whoTiles, "is-friends"));

      var forLocation = d.friendOf
        ? inCategory.filter(function (p) {
            return ((p.category || {}).friend_of || UNSPECIFIED) === d.friendOf;
          })
        : inCategory;
      var locTiles = groupBy(forLocation, "friend_location",
                             state.data.vocab.friend_locations).map(function (g) {
        return subTile(g.label === UNSPECIFIED ? "Not said" : g.label,
                       g.stats, d.location === g.label,
                       function () {
                         setDrill(d.category, d.friendOf,
                                  d.location === g.label ? null : g.label);
                       });
      });
      if (locTiles.length) els.overview.appendChild(subSection("Location", locTiles, "is-friends"));

    } else if (d.category === "Family") {
      var famTiles = groupBy(inCategory, "family_location",
                             state.data.vocab.family_locations).map(function (g) {
        return subTile(g.label === UNSPECIFIED ? "Not said" : g.label,
                       g.stats, d.location === g.label,
                       function () {
                         setDrill(d.category, null,
                                  d.location === g.label ? null : g.label);
                       });
      });
      if (famTiles.length) els.overview.appendChild(subSection("Family location", famTiles, "is-family"));
    }

    // ---- level 3: the guests themselves
    var shown = pool.filter(drillMatches);
    els.overview.appendChild(guestList(shown));
    els.count.textContent = statLine(statsFor(shown));
  }

  // ------------------------------------------------- undo a manual move
  function movedMembers(party) {
    return party.members.filter(function (m) {
      return m.moved && m.status === ATTENDING;
    });
  }

  function undoButton(party) {
    var moved = movedMembers(party);
    if (!moved.length) return null;
    var btn = el("button", "btn btn-undo btn-small",
      moved.length === 1 ? "Change status / undo move"
                         : "Change status / undo " + moved.length + " moves");
    btn.type = "button";
    btn.addEventListener("click", function () {
      openUndoDialog(party, moved);
    });
    return btn;
  }

  function openUndoDialog(party, moved) {
    els.modalTitle.textContent = "Undo move back to the original status?";
    els.modalConfirm.textContent = "Confirm undo";
    els.modalBody.textContent = "";

    var lead = el("p", "modal-lead");
    lead.appendChild(document.createTextNode("Return "));
    lead.appendChild(el("strong", null, party.name));
    lead.appendChild(document.createTextNode(
      moved.length === 1 ? " to its original status?"
                         : " to their original statuses?"));
    els.modalBody.appendChild(lead);

    els.modalBody.appendChild(el("p", "modal-note",
      "This undoes a manual move only. The record goes back to exactly what the "
      + "source CSV said, its confirmed headcount is dropped, and the reversal is "
      + "written to the audit log. Nothing else in this party is touched."));

    var rows = [];
    moved.forEach(function (member) {
      var row = el("div", "move-row");
      var head = el("p", "move-row-head");
      head.appendChild(el("strong", null, member.full_name));
      head.appendChild(el("span", "move-row-label", member.guest_label));
      row.appendChild(head);

      var facts = el("dl", "move-facts");
      [["Originally", member.source_status],
       ["Current status", member.status],
       ["Confirmed attending", member.people_count],
       ["Reverting to", member.source_status]].forEach(function (pair) {
        facts.appendChild(el("dt", null, pair[0]));
        facts.appendChild(el("dd", null, String(pair[1])));
      });
      row.appendChild(facts);

      var pick = el("label", "undo-pick");
      var box = el("input");
      box.type = "checkbox";
      box.checked = true;
      pick.appendChild(box);
      pick.appendChild(el("span", null, "Undo this one"));
      row.appendChild(pick);

      els.modalBody.appendChild(row);
      rows.push({ member: member, box: box });
    });

    // The category sits on the party, so only offer to clear it when the
    // reversal leaves nobody in this party attending. Otherwise clearing it
    // would strip the category from members who never moved.
    var catBox = null;
    function refreshCategoryPrompt() {
      var going = rows.filter(function (r) { return r.box.checked; })
                      .reduce(function (n, r) { return n + r.member.people_count; }, 0);
      var leaves = party.attending_people - going;
      var show = leaves <= 0 && (party.category || {}).category;
      if (catQuestion) catQuestion.hidden = !show;
      if (remainNote) {
        remainNote.textContent = leaves > 0
          ? leaves + (leaves === 1 ? " person" : " people") +
            " in this party stay attending, so the category is kept."
          : "";
        remainNote.hidden = leaves <= 0;
      }
    }

    var catQuestion = el("div", "undo-category");
    var catLabel = el("label", "undo-pick");
    catBox = el("input");
    catBox.type = "checkbox";
    catLabel.appendChild(catBox);
    catLabel.appendChild(el("span", null,
      "Also remove this party's category (" +
      ((party.category || {}).category || "none") + ")"));
    catQuestion.appendChild(catLabel);
    catQuestion.appendChild(el("p", "undo-hint",
      "Leave unticked to keep the categorisation for later."));
    catQuestion.hidden = true;
    els.modalBody.appendChild(catQuestion);

    var remainNote = el("p", "undo-hint");
    remainNote.hidden = true;
    els.modalBody.appendChild(remainNote);

    rows.forEach(function (r) {
      r.box.addEventListener("change", refreshCategoryPrompt);
    });
    refreshCategoryPrompt();

    els.modal.hidden = false;
    els.modalConfirm.disabled = false;
    els.modalConfirm.focus();

    els.modalConfirm.onclick = function () {
      var chosen = rows.filter(function (r) { return r.box.checked; });
      if (!chosen.length) {
        toast("Nothing selected to undo.", true);
        return;
      }
      els.modalConfirm.disabled = true;
      var removeCategory = !catQuestion.hidden && catBox.checked;

      // one request per record; the category question rides on the last
      var chain = Promise.resolve(null);
      chosen.forEach(function (r, index) {
        chain = chain.then(function () {
          return post("/api/revert", {
            record_key: r.member.record_key,
            remove_category: removeCategory && index === chosen.length - 1
          });
        });
      });

      chain.then(function (body) {
        closeModal();
        state.data.summary = body.summary;
        replaceParty(body.party);
        paintSummary(body.summary);
        buildTabs();
        renderCategoryBar();
        render();
        toast(party.name + " returned to " + body.reverted_to +
              (body.category_removed ? " · category removed" : ""));
      }).catch(function (err) {
        els.modalConfirm.disabled = false;
        toast(err.message, true);
      });
    };
  }

  // ------------------------------------------------------------- party
  function renderParty(party, activeStatus) {
    var headline = activeStatus
      ? (party.people_by_status[activeStatus] || 0)
      : party.total_people;

    var card = el("article", "party s-" + slug(activeStatus || party.primary_status));

    var head = el("div", "party-head");
    head.appendChild(el("span", "party-no", "#" + party.party_no));
    head.appendChild(el("h2", "party-name", party.name));

    if (party.is_group) {
      head.appendChild(el("span", "group-flag", party.member_count + " in party"));
    }

    var confirmedHere = activeStatus
      ? activeStatus === ATTENDING
      : party.members.every(function (m) { return m.confirmed; });
    head.appendChild(el("span", "headcount" + (confirmedHere ? "" : " headcount-est"),
      headline + (headline === 1 ? " person" : " people") +
      (confirmedHere ? "" : " (est.)")));

    (activeStatus ? [activeStatus] : party.statuses).forEach(function (status) {
      head.appendChild(el("span", "badge b-" + slug(status), status));
    });

    if (partyIsFlagged(party)) {
      head.appendChild(el("span", "dup-flag", "Possible duplicate — see Review"));
    }

    card.appendChild(head);

    var list = el("ul", "members");
    party.members.forEach(function (member) {
      var isActive = !activeStatus || member.status === activeStatus;
      list.appendChild(renderMember(member, isActive));
    });
    card.appendChild(list);

    var footer = el("div", "party-actions");
    if (activeStatus === ATTENDING) {
      footer.appendChild(categoryControl(party));
      var undoHere = undoButton(party);
      if (undoHere) {
        var undoRow = el("div", "party-undo");
        undoRow.appendChild(undoHere);
        footer.appendChild(undoRow);
      }
      card.appendChild(footer);
    } else if (activeStatus) {
      // Only the members carrying THIS status can move. A part-attending
      // party keeps its attending members out of the payload entirely.
      var movable = party.members.filter(function (m) {
        return m.status === activeStatus;
      });
      if (movable.length) {
        var heads = movable.reduce(function (n, m) { return n + m.people_count; }, 0);
        var move = el("button", "btn btn-move",
          "Move to Attending (" + heads + (heads === 1 ? " guest" : " guests") + ")");
        move.type = "button";
        move.addEventListener("click", function () {
          openMoveDialog(party, movable);
        });
        footer.appendChild(move);
        card.appendChild(footer);
      }
    }

    return card;
  }

  // ------------------------------------------------------------ review
  function renderReview() {
    els.results.hidden = true;
    els.toolbar.hidden = true;
    els.review.hidden = false;
    els.review.textContent = "";

    var summary = state.data.summary;
    var review = state.data.review;

    els.review.appendChild(el("h2", null, "Possible duplicates — for review"));
    els.review.appendChild(el("p", "review-intro",
      "Nothing here has been removed or deducted. The dashboard reports the verified " +
      summary.attending + " attending. These are same-person-invited-twice candidates " +
      "found by matching names and contact details — confirm or dismiss each one."));

    var banner = el("div", "review-banner");
    banner.appendChild(el("strong", null, "If every flagged pair is confirmed: "));
    banner.appendChild(document.createTextNode(
      "attending would fall from " + summary.attending + " to " +
      (summary.attending - review.high_confidence_people) + ", and pending from " +
      summary.pending + " to " + (summary.pending - review.medium_confidence_people) +
      ". Until then the headline figures stand as counted."));
    els.review.appendChild(banner);

    var grid = el("div", "dup-grid");
    state.data.duplicates.forEach(function (dup) {
      var conf = dup.confidence.toLowerCase();
      var card = el("div", "dup c-" + conf);

      var top = el("div", "dup-top");
      top.appendChild(el("span", "conf c-" + conf, dup.confidence));
      top.appendChild(el("span", "dup-bucket", "Affects: " + dup.bucket));
      if (dup.people_at_risk > 0) {
        top.appendChild(el("span", "dup-risk", dup.people_at_risk + " people at risk"));
      }
      card.appendChild(top);

      var pair = el("div", "dup-pair");
      pair.appendChild(el("div", "dup-rec", dup.record_a));
      pair.appendChild(el("div", "dup-rec", dup.record_b));
      card.appendChild(pair);

      card.appendChild(el("p", "dup-why", dup.matches));
      card.appendChild(el("p", "dup-effect", dup.effect));
      grid.appendChild(card);
    });
    els.review.appendChild(grid);

    var notes = el("div", "notes");
    notes.appendChild(el("h3", null, "Data-quality notes"));
    var list = el("ul");
    state.data.data_quality_notes.forEach(function (note) {
      var li = el("li");
      li.appendChild(el("strong", null, note.subject + ": "));
      li.appendChild(document.createTextNode(note.note));
      list.appendChild(li);
    });
    notes.appendChild(list);
    els.review.appendChild(notes);
  }

  // ------------------------------------------------------------ render
  function render() {
    if (state.tab === REVIEW_TAB) {
      els.categoryBar.hidden = true;
      els.overview.hidden = true;
      renderReview();
      return;
    }

    els.review.hidden = true;
    els.toolbar.hidden = false;
    els.results.textContent = "";

    var activeStatus = state.tab === ALL_TAB ? null : state.tab;

    if (activeStatus === ATTENDING && state.view === "overview") {
      els.results.hidden = true;
      els.overview.hidden = false;
      renderOverview();
      return;
    }
    els.overview.hidden = true;
    els.results.hidden = false;
    var query = state.query.trim().toLowerCase();

    var matches = state.data.parties.filter(function (party) {
      if (activeStatus && party.statuses.indexOf(activeStatus) === -1) return false;
      if (activeStatus === ATTENDING && !partyMatchesFilter(party)) return false;
      return !query || party.search_blob.indexOf(query) !== -1;
    });

    var people = matches.reduce(function (sum, party) {
      return sum + (activeStatus ? (party.people_by_status[activeStatus] || 0)
                                 : party.total_people);
    }, 0);

    var estimated = activeStatus && activeStatus !== ATTENDING;
    els.count.textContent = matches.length
      ? matches.length + (matches.length === 1 ? " party" : " parties") +
        " · " + people + (people === 1 ? " person" : " people") +
        (estimated ? " (estimated)" : "")
      : "";

    if (!matches.length) {
      els.results.appendChild(el("div", "empty",
        query ? 'No guests match "' + state.query.trim() + '".'
              : "No guests in this status."));
      return;
    }

    var fragment = document.createDocumentFragment();
    matches.forEach(function (party) {
      fragment.appendChild(renderParty(party, activeStatus));
    });
    els.results.appendChild(fragment);
  }

  // ------------------------------------------------------------- events
  var searchTimer = null;
  els.search.addEventListener("input", function () {
    state.query = els.search.value;
    els.clear.hidden = !state.query;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, 120);
  });

  els.clear.addEventListener("click", function () {
    els.search.value = "";
    state.query = "";
    els.clear.hidden = true;
    els.search.focus();
    render();
  });

  [].forEach.call(els.viewSwitch.querySelectorAll(".view-btn"), function (btn) {
    btn.addEventListener("click", function () {
      state.view = btn.getAttribute("data-view");
      saveView(state.view);
      renderViewSwitch();
      render();
    });
  });

  els.contacts.addEventListener("change", function () {
    state.showContacts = els.contacts.checked;
    render();
  });

  // --------------------------------------------------------------- boot
  fetch("/api/data", { credentials: "same-origin" })
    .then(function (response) {
      if (response.status === 401 || response.redirected) {
        window.location.href = "/login";
        throw new Error("not signed in");
      }
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    })
    .then(function (data) {
      state.data = data;
      state.flaggedNames = buildFlaggedNames(data.duplicates);
      state.view = loadView();
      buildTabs();
      renderCategoryBar();
      renderViewSwitch();
      render();
    })
    .catch(function (error) {
      els.results.textContent = "";
      els.results.appendChild(el("div", "empty",
        "Could not load the guest list: " + error.message));
    });
})();
