/**
 * Smart Shelf — what to do about the produce on this shelf, right now.
 *
 * Reads the same endpoints as before (/devices, /devices/{id}/status,
 * /devices/{id}/history, /devices/{id}/forecast) and keeps the 45s poll.
 * Everything shown here is either an API field or derived from one; nothing
 * on this page is invented.
 *
 * Bilingual: Hindi and English, switched by the control in the masthead and
 * remembered per browser. Every user-facing string lives in COPY below — none
 * are built inline — so a third language is a new key, not a code change.
 */

const POLL_MS = 45000;
const ACK_TTL_MS = 8 * 60 * 60 * 1000; // an acknowledgement is good for a shift
const ACK_SRI_SLACK = 0.03;            // ...unless risk climbs past where it was
const LANG_KEY = "smartshelf.lang";
const DEFAULT_LANG = "hi";             // the shopkeeper's language leads

// Each commodity is drawn in the colour of the actual produce.
const PRODUCE_COLOUR = {
  tomato: "#C33B2B",
  onion: "#8E4A63",
  potato: "#A87A3C",
  leafy_greens: "#4C7A2C",
};
const PRODUCE_FALLBACK = "#5A6B5F";

/* ------------------------------------------------------------------ *
 * every word on the page
 * ------------------------------------------------------------------ */

const COPY = {
  en: {
    other: "हिन्दी",                       // the switch names where it takes you
    otherAria: "Switch to Hindi",
    brand: "Smart Shelf",
    shelfLabel: "Shelf",
    chooseShelf: "Choose a shelf",

    produce: { tomato: "Tomato", onion: "Onion", potato: "Potato", leafy_greens: "Leafy greens" },
    who: (name, id) => name + " on " + id,
    thisShelf: "This shelf",

    bootWho: "Reading the shelf",
    bootSay: "Just a moment.",

    sayAct: "Sell or move these today.",
    sayWatch: "Keep an eye on these.",
    sayCalm: "These are keeping well.",
    ackedAct: "You've moved these.",
    ackedWatch: "You've seen this.",
    ackAct: "I've moved them",
    ackWatch: "I've seen this",

    noCommodity: "No produce assigned to this shelf.",
    noCommodityFix: "Assign a commodity to start tracking spoilage.",
    noReadings: "No readings from this shelf yet.",
    noReadingsFix: "Check that the sensor node is powered on and connected.",

    riskNow: "Spoilage risk is high right now.",
    riskEasing: "Risk is easing.",
    nothingToDo: "Nothing to do today.",
    steady: "Holding steady for now.",
    learning: "Still learning this shelf. The trend needs a few more readings.",
    riskIn: (d) => d.charAt(0).toUpperCase() + d.slice(1) + " before spoilage risk.",
    duration: {
      minutes: (n) => "about " + n + " minutes",
      hour: "about an hour",
      hours: (n) => "about " + n + " hours",
      day: "more than a day",
    },

    fanSince: (t) => "The fan has been on since " + t + ".",
    fanOn: "The fan is on.",
    fanOff: "The fan is off.",
    climate: (t, h) => "It's " + t + "° and " + h + "% humid.",

    others: "Other shelves",
    rowAct: "needs attention",
    rowWatch: "worth a look",
    rowCalm: "holding steady",
    rowUnassigned: "nothing assigned",
    rowNoReadings: "no readings yet",
    rowUnreachable: "not reachable",

    showReadings: "Show the readings",
    nSri: "Spoilage index",
    nTemp: "Temperature",
    nHum: "Humidity",
    nGas: "Gas reading",
    nFanAt: "Fan turns on at",
    nAlertAt: "Alert level",
    nSlope: "Change per minute",
    noteEstimate: "The dotted line extrapolates the recent trend. It is an estimate, not a promise.",
    noteLearning: "The forecast line needs at least four spaced-out readings before it can be drawn.",
    noteIdle: "Readings will appear here once the shelf reports in.",

    chartMeasured: "Measured",
    chartExpected: "Expected",
    chartFanOn: "Fan turns on",
    chartAlert: "Alert level",
    axisHour: "h a",

    updated: (t) => "Updated " + t + ", and every 45 seconds.",
    lastTried: (t) => "Last tried " + t + ".",
    unreachable: "Can't reach the shelf service.",
    unreachableFix: "Check that the backend is running, then refresh this page.",
    noShelves: "No shelves registered yet.",
    noShelvesFix: "Register a shelf node to begin.",
  },

  hi: {
    other: "English",
    otherAria: "अंग्रेज़ी में बदलें",
    brand: "स्मार्ट शेल्फ",
    shelfLabel: "शेल्फ",
    chooseShelf: "शेल्फ चुनें",

    produce: {
      tomato: "टमाटर",
      onion: "प्याज़",
      potato: "आलू",
      leafy_greens: "हरी पत्तेदार सब्ज़ी",
    },
    who: (name, id) => id + " पर " + name,
    thisShelf: "यह शेल्फ",

    bootWho: "शेल्फ पढ़ी जा रही है",
    bootSay: "एक पल।",

    sayAct: "इन्हें आज ही बेच दें या हटा दें।",
    sayWatch: "इन पर नज़र रखें।",
    sayCalm: "ये ठीक हालत में हैं।",
    ackedAct: "आपने इन्हें हटा दिया है।",
    ackedWatch: "आपने इसे देख लिया है।",
    ackAct: "मैंने हटा दिए",
    ackWatch: "देख लिया",

    noCommodity: "इस शेल्फ पर कोई सामान तय नहीं है।",
    noCommodityFix: "खराबी पर नज़र रखने के लिए सामान चुनें।",
    noReadings: "इस शेल्फ से अभी कोई रीडिंग नहीं आई।",
    noReadingsFix: "देखें कि सेंसर चालू है और जुड़ा हुआ है।",

    riskNow: "अभी खराब होने का ख़तरा ज़्यादा है।",
    riskEasing: "ख़तरा कम हो रहा है।",
    nothingToDo: "आज कुछ करने की ज़रूरत नहीं।",
    steady: "फ़िलहाल हालत स्थिर है।",
    learning: "अभी इस शेल्फ को समझा जा रहा है। रुझान के लिए कुछ और रीडिंग चाहिए।",
    riskIn: (d) => "खराब होने के ख़तरे में " + d + " बाकी।",
    duration: {
      minutes: (n) => "करीब " + n + " मिनट",
      hour: "करीब एक घंटा",
      hours: (n) => "करीब " + n + " घंटे",
      day: "एक दिन से ज़्यादा",
    },

    fanSince: (t) => "पंखा " + t + " से चल रहा है।",
    fanOn: "पंखा चल रहा है।",
    fanOff: "पंखा बंद है।",
    climate: (t, h) => "तापमान " + t + "° और नमी " + h + "% है।",

    others: "दूसरी शेल्फ",
    rowAct: "ध्यान दें",
    rowWatch: "देख लें",
    rowCalm: "ठीक है",
    rowUnassigned: "कुछ तय नहीं",
    rowNoReadings: "कोई रीडिंग नहीं",
    rowUnreachable: "संपर्क नहीं",

    showReadings: "रीडिंग देखें",
    nSri: "खराबी सूचकांक",
    nTemp: "तापमान",
    nHum: "नमी",
    nGas: "गैस रीडिंग",
    nFanAt: "पंखा चालू स्तर",
    nAlertAt: "चेतावनी स्तर",
    nSlope: "प्रति मिनट बदलाव",
    noteEstimate: "बिंदुदार रेखा हाल के रुझान का अनुमान है। यह अंदाज़ा है, पक्का वादा नहीं।",
    noteLearning: "अनुमान रेखा खींचने के लिए कम से कम चार अलग-अलग समय की रीडिंग चाहिए।",
    noteIdle: "शेल्फ से जानकारी आते ही रीडिंग यहाँ दिखेंगी।",

    chartMeasured: "मापा गया",
    chartExpected: "अनुमान",
    chartFanOn: "पंखा चालू",
    chartAlert: "चेतावनी स्तर",
    axisHour: "HH",                       // 24-hour on the axis reads cleanly in Hindi

    updated: (t) => t + " पर अपडेट, और हर 45 सेकंड में।",
    lastTried: (t) => "आख़िरी कोशिश " + t + "।",
    unreachable: "शेल्फ सेवा से संपर्क नहीं हो पा रहा।",
    unreachableFix: "देखें कि बैकएंड चल रहा है, फिर यह पेज रिफ़्रेश करें।",
    noShelves: "अभी कोई शेल्फ दर्ज नहीं है।",
    noShelvesFix: "शुरू करने के लिए एक शेल्फ दर्ज करें।",
  },
};

const els = {
  brand: document.getElementById("brand"),
  langToggle: document.getElementById("lang-toggle"),
  shelfLabel: document.getElementById("shelf-label"),
  picker: document.getElementById("shelf-picker"),
  crate: document.getElementById("crate"),
  who: document.getElementById("crate-who"),
  say: document.getElementById("crate-say"),
  when: document.getElementById("crate-when"),
  facts: document.getElementById("crate-facts"),
  ack: document.getElementById("ack"),
  others: document.getElementById("others"),
  othersTitle: document.querySelector(".others__title"),
  rows: document.getElementById("rows"),
  evidence: document.getElementById("evidence"),
  evidenceSummary: document.querySelector(".evidence__summary"),
  numbers: document.getElementById("numbers"),
  note: document.getElementById("evidence-note"),
  footTime: document.getElementById("foot-time"),
};

let shelves = [];
let currentDeviceId = null;
let chart = null;
let lang = DEFAULT_LANG;

const t = () => COPY[lang];

/* ------------------------------------------------------------------ *
 * small helpers
 * ------------------------------------------------------------------ */

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(url + " responded " + res.status);
  return res.json();
}

function produceName(type) {
  if (!type) return "";
  return t().produce[type] || type.replace(/_/g, " ");
}

function produceColour(type) {
  return PRODUCE_COLOUR[type] || PRODUCE_FALLBACK;
}

/**
 * Shop hours. English reads 8:04 am; Hindi names the part of the day, which is
 * how the time is actually said — पूर्वाह्न is correct but nobody speaks it.
 */
function clockTime(date) {
  if (lang === "en") {
    return date.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true });
  }
  const h = date.getHours();
  const mm = String(date.getMinutes()).padStart(2, "0");
  const h12 = h % 12 === 0 ? 12 : h % 12;
  let part;
  if (h >= 4 && h < 12) part = "सुबह";
  else if (h >= 12 && h < 16) part = "दोपहर";
  else if (h >= 16 && h < 19) part = "शाम";
  else part = "रात";
  return part + " " + h12 + ":" + mm;
}

/** Plain-language duration. Deliberately vague — the trend is an estimate. */
function humanDuration(minutes) {
  const d = t().duration;
  if (minutes < 45) return d.minutes(Math.max(5, Math.round(minutes / 5) * 5));
  if (minutes < 90) return d.hour;
  if (minutes < 20 * 60) return d.hours(Math.round(minutes / 60));
  return d.day;
}

/* ------------------------------------------------------------------ *
 * reading the shelf
 * ------------------------------------------------------------------ */

/**
 * How long the fan has been running, by walking back through history while
 * fan_commanded stays true. Returns the Date it came on, or null if it's off.
 */
function fanOnSince(historyNewestFirst) {
  if (!historyNewestFirst.length || !historyNewestFirst[0].fan_commanded) return null;
  let since = historyNewestFirst[0].device_timestamp;
  for (const r of historyNewestFirst) {
    if (!r.fan_commanded) break;
    since = r.device_timestamp;
  }
  return new Date(since);
}

/** Minutes until SRI is forecast to cross the alert threshold, or null. */
function minutesUntilRisk(sri, alertThreshold, slopePerMin) {
  if (sri === null || slopePerMin === null || slopePerMin === undefined) return null;
  if (slopePerMin <= 0.00001) return null;
  if (sri >= alertThreshold) return 0;
  return (alertThreshold - sri) / slopePerMin;
}

function readAck(deviceId) {
  try {
    return JSON.parse(localStorage.getItem("smartshelf.ack." + deviceId));
  } catch (err) {
    return null;
  }
}

function writeAck(deviceId, sri) {
  try {
    localStorage.setItem(
      "smartshelf.ack." + deviceId,
      JSON.stringify({ at: Date.now(), sri: sri })
    );
  } catch (err) {
    /* private mode — the button simply won't stick */
  }
}

/**
 * Turn the algorithm's numbers into a state and the words for it.
 * States: idle (nothing to say), calm, watch, act.
 */
function assess(status, forecast, history) {
  const C = t();
  const commodity = status && status.active_commodity;
  const latest = status && status.latest_reading;

  if (!commodity) {
    return {
      state: "idle", who: C.thisShelf, say: C.noCommodity,
      when: C.noCommodityFix, facts: "", commodity: null,
    };
  }

  const name = produceName(commodity);

  if (!latest || latest.sri === null || latest.sri === undefined) {
    return {
      state: "idle", who: C.who(name, status.device_id), say: C.noReadings,
      when: C.noReadingsFix, facts: "", commodity: commodity,
    };
  }

  const sri = Number(latest.sri);
  const fanOn = forecast && forecast.sri_fan_on !== null ? forecast.sri_fan_on : 0.6;
  const alert = forecast && forecast.alert_threshold !== null ? forecast.alert_threshold : 0.7;
  const slope = forecast ? forecast.trend_slope_per_min : null;
  const learning = !forecast || forecast.insufficient_data;

  const state = sri >= alert ? "act" : sri >= fanOn ? "watch" : "calm";

  // --- the instruction ---------------------------------------------------
  let say = state === "act" ? C.sayAct : state === "watch" ? C.sayWatch : C.sayCalm;

  // --- the timing ---------------------------------------------------------
  let when;
  if (learning) {
    when = C.learning;
  } else if (state === "act") {
    when = C.riskNow;
  } else if (slope !== null && slope < -0.00001) {
    when = C.riskEasing;
  } else {
    const mins = minutesUntilRisk(sri, alert, slope);
    if (mins === null) when = state === "calm" ? C.nothingToDo : C.steady;
    else when = C.riskIn(humanDuration(mins));
  }

  // --- the supporting facts, as sentences ---------------------------------
  const since = fanOnSince(history);
  const parts = [];
  if (status.fan_command === "on") parts.push(since ? C.fanSince(clockTime(since)) : C.fanOn);
  else parts.push(C.fanOff);
  if (latest.temp_c !== undefined && latest.humidity_pct !== undefined) {
    parts.push(C.climate(Number(latest.temp_c).toFixed(1), Math.round(latest.humidity_pct)));
  }

  // --- did they already deal with it? -------------------------------------
  const ack = readAck(status.device_id);
  const stillAcked = ack && Date.now() - ack.at < ACK_TTL_MS && sri <= ack.sri + ACK_SRI_SLACK;

  // The button names the action it confirms, and the confirmation echoes it.
  const ackLabel = state === "act" ? C.ackAct : C.ackWatch;
  if (stillAcked && state !== "calm") {
    say = state === "act" ? C.ackedAct : C.ackedWatch;
  }

  return {
    state, who: C.who(name, status.device_id), say, when,
    facts: parts.join(" "),
    commodity, sri, fanOn, alert, slope, learning, latest,
    ackLabel,
    canAck: (state === "act" || state === "watch") && !stillAcked,
  };
}

/* ------------------------------------------------------------------ *
 * rendering
 * ------------------------------------------------------------------ */

function renderCrate(view) {
  els.crate.dataset.state = view.state;
  els.crate.style.setProperty("--produce", produceColour(view.commodity));
  els.who.textContent = view.who;
  els.say.textContent = view.say;
  els.when.textContent = view.when;
  els.facts.textContent = view.facts;
  els.ack.hidden = !view.canAck;
  if (view.ackLabel) els.ack.textContent = view.ackLabel;
}

function renderOthers(others) {
  const C = t();
  els.rows.replaceChildren();
  if (!others.length) {
    els.others.hidden = true;
    return;
  }
  els.others.hidden = false;

  for (const o of others) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "row";
    btn.dataset.state = o.state;
    btn.style.setProperty("--produce", produceColour(o.commodity));

    const who = document.createElement("span");
    who.className = "row__who";
    who.textContent = o.commodity ? produceName(o.commodity) : o.deviceId;

    const how = document.createElement("span");
    how.className = "row__how";
    how.textContent = C[o.summaryKey];

    btn.append(who, how);
    btn.addEventListener("click", () => {
      els.picker.value = o.deviceId;
      currentDeviceId = o.deviceId;
      refresh();
    });

    li.append(btn);
    els.rows.append(li);
  }
}

function renderNumbers(view) {
  const C = t();
  els.numbers.replaceChildren();
  if (view.state === "idle") {
    els.note.textContent = C.noteIdle;
    return;
  }

  const rows = [
    [C.nSri, view.sri.toFixed(3)],
    [C.nTemp, Number(view.latest.temp_c).toFixed(1) + "°C"],
    [C.nHum, Math.round(view.latest.humidity_pct) + "%"],
    [C.nGas, Math.round(view.latest.gas_raw)],
    [C.nFanAt, view.fanOn.toFixed(2)],
    [C.nAlertAt, view.alert.toFixed(2)],
  ];
  if (!view.learning && view.slope !== null) {
    rows.push([C.nSlope, (view.slope >= 0 ? "+" : "") + view.slope.toFixed(4)]);
  }

  for (const [label, value] of rows) {
    const wrap = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    wrap.append(dt, dd);
    els.numbers.append(wrap);
  }

  els.note.textContent = view.learning ? C.noteLearning : C.noteEstimate;
}

function buildChart() {
  const ctx = document.getElementById("sri-chart").getContext("2d");
  chart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        { data: [], borderColor: PRODUCE_FALLBACK, backgroundColor: "transparent",
          borderWidth: 2.5, pointRadius: 0, tension: 0.25 },
        { data: [], borderColor: PRODUCE_FALLBACK, backgroundColor: "transparent",
          borderWidth: 2, borderDash: [3, 4], pointRadius: 0, tension: 0.25 },
        { data: [], borderColor: "#8A6A10", borderWidth: 1, borderDash: [7, 5], pointRadius: 0 },
        { data: [], borderColor: "#B9271A", borderWidth: 1, borderDash: [7, 5], pointRadius: 0 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      scales: {
        x: {
          type: "time",
          time: { displayFormats: { hour: "h a", minute: "h:mm" } },
          grid: { color: "#DFE7DC" },
          ticks: { color: "#5A6B5F", font: { family: "Anek Latin", size: 12 }, maxRotation: 0 },
        },
        y: {
          min: 0, max: 1,
          grid: { color: "#DFE7DC" },
          ticks: { color: "#5A6B5F", font: { family: "Anek Latin", size: 12 }, stepSize: 0.25 },
        },
      },
      plugins: {
        legend: { labels: { color: "#14251B", boxWidth: 14, boxHeight: 2,
          font: { family: "Anek Latin", size: 12 } } },
        tooltip: { backgroundColor: "#14251B",
          titleFont: { family: "Anek Latin" }, bodyFont: { family: "Anek Latin" } },
      },
    },
  });
  applyChartCopy();
}

function applyChartCopy() {
  if (!chart) return;
  const C = t();
  const labels = [C.chartMeasured, C.chartExpected, C.chartFanOn, C.chartAlert];
  chart.data.datasets.forEach((d, i) => { d.label = labels[i]; });
  chart.options.scales.x.time.displayFormats.hour = C.axisHour;
  const face = lang === "hi" ? "Anek Devanagari" : "Anek Latin";
  chart.options.scales.x.ticks.font.family = face;
  chart.options.scales.y.ticks.font.family = face;
  chart.options.plugins.legend.labels.font.family = face;
  chart.update();
}

function renderChart(view, history, forecast) {
  if (!chart) return;
  const colour = produceColour(view.commodity);

  const measured = [...history]
    .filter((r) => r.sri !== null && r.sri !== undefined)
    .map((r) => ({ x: new Date(r.device_timestamp), y: Number(r.sri) }))
    .sort((a, b) => a.x - b.x);

  let expected = [];
  if (forecast && forecast.forecast && forecast.forecast.length) {
    expected = forecast.forecast.map((p) => ({
      x: new Date(p.timestamp), y: Number(p.predicted_sri),
    }));
    // join the forecast to the last measured point so the line is continuous
    if (measured.length) expected.unshift(measured[measured.length - 1]);
  }

  const from = measured.length ? measured[0].x : new Date(Date.now() - 6 * 3600e3);
  const to = expected.length ? expected[expected.length - 1].x : new Date();

  chart.data.datasets[0].borderColor = colour;
  chart.data.datasets[0].data = measured;
  chart.data.datasets[1].borderColor = colour;
  chart.data.datasets[1].data = expected;
  chart.data.datasets[2].data = [{ x: from, y: view.fanOn }, { x: to, y: view.fanOn }];
  chart.data.datasets[3].data = [{ x: from, y: view.alert }, { x: to, y: view.alert }];
  chart.update();
}

/** Never let a missing bit of chrome stop the shelf data from loading. */
function setText(el, text) {
  if (el) el.textContent = text;
}

/** Chrome that doesn't depend on a reading. */
function renderChrome() {
  const C = t();
  document.documentElement.lang = lang;
  document.title = C.brand;
  setText(els.brand, C.brand);
  setText(els.shelfLabel, C.shelfLabel);
  setText(els.langToggle, C.other);
  setText(els.othersTitle, C.others);
  setText(els.evidenceSummary, C.showReadings);
  if (els.picker) els.picker.setAttribute("aria-label", C.chooseShelf);
  if (els.langToggle) els.langToggle.setAttribute("aria-label", C.otherAria);
  applyChartCopy();
}

/* ------------------------------------------------------------------ *
 * loading
 * ------------------------------------------------------------------ */

/** A one-line state for a shelf that isn't the one on screen. */
async function summariseShelf(deviceId) {
  try {
    const status = await getJSON("/devices/" + deviceId + "/status");
    const commodity = status.active_commodity;
    const latest = status.latest_reading;

    if (!commodity) return { deviceId, commodity: null, state: "idle", summaryKey: "rowUnassigned" };
    if (!latest || latest.sri === null || latest.sri === undefined) {
      return { deviceId, commodity, state: "idle", summaryKey: "rowNoReadings" };
    }

    const forecast = await getJSON("/devices/" + deviceId + "/forecast?horizon_minutes=60&step_minutes=15");
    const sri = Number(latest.sri);
    const fanOn = forecast.sri_fan_on !== null ? forecast.sri_fan_on : 0.6;
    const alert = forecast.alert_threshold !== null ? forecast.alert_threshold : 0.7;

    if (sri >= alert) return { deviceId, commodity, state: "act", summaryKey: "rowAct" };
    if (sri >= fanOn) return { deviceId, commodity, state: "watch", summaryKey: "rowWatch" };
    return { deviceId, commodity, state: "calm", summaryKey: "rowCalm" };
  } catch (err) {
    return { deviceId, commodity: null, state: "idle", summaryKey: "rowUnreachable" };
  }
}

async function refresh() {
  if (!currentDeviceId) return;
  const C = t();

  try {
    const [status, forecast, history] = await Promise.all([
      getJSON("/devices/" + currentDeviceId + "/status"),
      getJSON("/devices/" + currentDeviceId + "/forecast?horizon_minutes=360&step_minutes=15"),
      getJSON("/devices/" + currentDeviceId + "/history?limit=200"),
    ]);

    const view = assess(status, forecast, history);
    renderCrate(view);
    renderNumbers(view);
    renderChart(view, history, forecast);

    els.ack.onclick = () => {
      writeAck(currentDeviceId, view.sri);
      refresh();
    };

    const others = await Promise.all(
      shelves.filter((s) => s.device_id !== currentDeviceId).map((s) => summariseShelf(s.device_id))
    );
    renderOthers(others);

    els.footTime.textContent = C.updated(clockTime(new Date()));
  } catch (err) {
    els.crate.dataset.state = "idle";
    els.say.textContent = C.unreachable;
    els.when.textContent = C.unreachableFix;
    els.facts.textContent = "";
    els.ack.hidden = true;
    els.footTime.textContent = C.lastTried(clockTime(new Date()));
  }
}

async function loadShelves() {
  const C = t();
  try {
    shelves = await getJSON("/devices");
  } catch (err) {
    shelves = [];
  }

  els.picker.replaceChildren();
  for (const s of shelves) {
    const opt = document.createElement("option");
    opt.value = s.device_id;
    opt.textContent = s.device_id;
    els.picker.append(opt);
  }

  if (shelves.length) {
    currentDeviceId = shelves[0].device_id;
    els.picker.value = currentDeviceId;
    refresh();
  } else {
    els.crate.dataset.state = "idle";
    els.who.textContent = C.thisShelf;
    els.say.textContent = C.noShelves;
    els.when.textContent = C.noShelvesFix;
  }
}

function setLang(next) {
  lang = COPY[next] ? next : DEFAULT_LANG;
  try {
    localStorage.setItem(LANG_KEY, lang);
  } catch (err) {
    /* private mode — the choice just won't persist */
  }
  renderChrome();
  refresh();
}

els.picker.addEventListener("change", (e) => {
  currentDeviceId = e.target.value;
  refresh();
});

els.langToggle.addEventListener("click", () => setLang(lang === "hi" ? "en" : "hi"));

// The canvas sits inside a closed <details>, so it has no size until the
// shopkeeper opens it. Re-measure on the way open or the plot draws blank.
els.evidence.addEventListener("toggle", (e) => {
  if (e.target.open && chart) chart.resize();
});

window.addEventListener("DOMContentLoaded", () => {
  let saved = null;
  try {
    saved = localStorage.getItem(LANG_KEY);
  } catch (err) {
    /* fall through to the default */
  }
  lang = COPY[saved] ? saved : DEFAULT_LANG;

  setText(els.who, t().bootWho);
  setText(els.say, t().bootSay);

  buildChart();
  renderChrome();
  loadShelves();
  setInterval(refresh, POLL_MS);
});
