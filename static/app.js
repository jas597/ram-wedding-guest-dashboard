/* Ram's Wedding guest-list dashboard.
 * All guest text is inserted with textContent, never innerHTML, so names and
 * messages from the CSV can never be interpreted as markup. */

(function () {
  "use strict";

  var REVIEW_TAB = "__review__";
  var ALL_TAB = "__all__";

  var state = {
    data: null,
    tab: ALL_TAB,
    query: "",
    showContacts: true,
    flaggedNames: new Set()
  };

  var els = {
    tabs: document.getElementById("tabs"),
    results: document.getElementById("results"),
    review: document.getElementById("review"),
    toolbar: document.getElementById("toolbar"),
    search: document.getElementById("search"),
    clear: document.getElementById("clear-search"),
    contacts: document.getElementById("show-contacts"),
    count: document.getElementById("result-count")
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
        buildTabs();
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
    body.appendChild(nameRow);

    var facts = el("p", "facts");
    facts.appendChild(fact("People", member.people_count));
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

    head.appendChild(el("span", "headcount",
      headline + (headline === 1 ? " person" : " people")));

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
      renderReview();
      return;
    }

    els.review.hidden = true;
    els.results.hidden = false;
    els.toolbar.hidden = false;
    els.results.textContent = "";

    var activeStatus = state.tab === ALL_TAB ? null : state.tab;
    var query = state.query.trim().toLowerCase();

    var matches = state.data.parties.filter(function (party) {
      if (activeStatus && !(party.people_by_status[activeStatus] >= 0 &&
          party.statuses.indexOf(activeStatus) !== -1)) {
        return false;
      }
      return !query || party.search_blob.indexOf(query) !== -1;
    });

    var people = matches.reduce(function (sum, party) {
      return sum + (activeStatus ? (party.people_by_status[activeStatus] || 0)
                                 : party.total_people);
    }, 0);

    els.count.textContent = matches.length
      ? matches.length + (matches.length === 1 ? " party" : " parties") +
        " · " + people + (people === 1 ? " person" : " people")
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
      buildTabs();
      render();
    })
    .catch(function (error) {
      els.results.textContent = "";
      els.results.appendChild(el("div", "empty",
        "Could not load the guest list: " + error.message));
    });
})();
