/**
 * Metra Train Tracker card, served automatically by the integration.
 *
 * Displays the schedule held by a station's "Next arrival" sensor. Every option
 * except `entity` is optional and can be set in the card's visual editor:
 *
 *   type: custom:metra-train-tracker-card
 *   entity: sensor.up_w_lombard_inbound_next_arrival
 *   layout: list            # list, compact or next_train
 *   time_display: expected  # expected, scheduled or both
 *   time_format: 12h        # 12h, 24h or raw
 *   countdown: minutes      # minutes, auto or off
 *   alerts: summary         # summary, full or hidden
 *   favorites: highlight    # mark, highlight or only
 *   pin_favorite: false     # always show the next favorite, even past the list
 *   max_trains: 3           # up to the station's "Trains to show" setting
 *   title: My commute
 *   show_header: true
 *   show_origin: false
 */

const STALE_AFTER_MS = 5 * 60 * 1000;
const REFRESH_MS = 30 * 1000;
const AUTO_COUNTDOWN_MINUTES = 30;
const LONG_COUNTDOWN_MINUTES = 90;
const SCHEDULE_TIME_ZONE = "America/Chicago";
const NEXT_TRAIN_FOLLOWING = 2;

const CHOICES = {
  layout: ["list", "compact", "next_train"],
  time_display: ["expected", "scheduled", "both"],
  time_format: ["12h", "24h", "raw"],
  countdown: ["minutes", "auto", "off"],
  alerts: ["summary", "full", "hidden"],
  favorites: ["mark", "highlight", "only"],
};

const DEFAULTS = {
  layout: "list",
  time_display: "expected",
  time_format: "12h",
  countdown: "minutes",
  alerts: "summary",
  favorites: "highlight",
  pin_favorite: false,
  show_header: true,
  show_origin: false,
};

const STYLES = `
  :host {
    --mtt-fg: var(--primary-text-color, #212121);
    --mtt-bg: var(--ha-card-background, var(--card-background-color, #fff));
    --mtt-muted: var(--secondary-text-color, #727272);
    --mtt-rule: var(--divider-color, #e0e0e0);
    --mtt-late: var(--error-color, #db4437);
    --mtt-good: var(--success-color, #43a047);
    --mtt-warn: var(--warning-color, #ffa600);
  }
  ha-card {
    display: flex;
    flex-direction: column;
    height: 100%;
    box-sizing: border-box;
    padding: 14px 16px;
    color: var(--mtt-fg);
    overflow: hidden;
  }
  .header { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
  .header-text { min-width: 0; }
  .title { font-size: 1.15rem; font-weight: 600; line-height: 1.25; }
  .subtitle { font-size: 0.8rem; color: var(--mtt-muted); }
  .chip-row { display: flex; justify-content: flex-end; margin-bottom: 6px; }
  .chip {
    flex: 0 0 auto;
    border: 0;
    border-radius: 999px;
    padding: 4px 10px;
    font: inherit;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--mtt-warn);
    background: color-mix(in srgb, var(--mtt-warn) 16%, transparent);
    cursor: pointer;
    white-space: nowrap;
  }
  .alert { border-left: 4px solid var(--mtt-warn); padding: 2px 0 2px 9px; margin: 6px 0 8px; }
  .alert-header { font-size: 0.9rem; font-weight: 600; }
  .alert-description { font-size: 0.8rem; line-height: 1.35; color: var(--mtt-muted); white-space: pre-line; }

  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 0.875rem;
    font-weight: 700;
    white-space: nowrap;
    color: var(--mtt-bg);
    background: var(--mtt-fg);
  }
  .star { margin-left: 4px; color: var(--mtt-warn); }
  .old-time { margin-left: 6px; font-size: 0.8rem; color: var(--mtt-muted); text-decoration: line-through; }
  .scheduled-time { margin-left: 6px; font-size: 0.8rem; color: var(--mtt-muted); }
  .late { color: var(--mtt-late); font-weight: 600; }
  .good { color: var(--mtt-good); }
  .muted { color: var(--mtt-muted); }

  .row { display: grid; grid-template-columns: 72px 1fr auto; gap: 8px; align-items: center; padding: 9px 0; border-top: 1px solid var(--mtt-rule); }
  .row:first-child { border-top: 0; }
  .time { font-size: 1.05rem; font-weight: 600; }
  .details { min-width: 0; }
  .status { font-size: 0.8rem; margin-top: 1px; }
  .countdown { font-weight: 600; text-align: right; white-space: nowrap; }
  .row.highlight, .compact-row.highlight {
    margin: 0 -16px;
    padding-left: 13px;
    padding-right: 16px;
    border-left: 3px solid var(--mtt-warn);
    background: color-mix(in srgb, var(--mtt-warn) 14%, transparent);
  }
  .pin-label { padding: 8px 0 2px; font-size: 0.7rem; color: var(--mtt-muted); border-top: 1px dashed var(--mtt-rule); }
  .pin-label + .row { border-top: 0; }

  .compact-row { display: grid; grid-template-columns: 64px 1fr auto auto; gap: 8px; align-items: center; padding: 4px 0; font-size: 0.875rem; }
  .compact-row .badge { font-size: 0.8rem; }
  .compact-row .time { font-size: inherit; }
  .compact-row .countdown { min-width: 44px; }
  .compact-delay { font-size: 0.8rem; }

  .hero-time { font-size: 2.75rem; font-weight: 700; line-height: 1.1; }
  .hero-info { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 8px; margin-top: 4px; }
  .hero-info .old-time { margin-left: 0; }
  .hero-then { margin-top: 10px; padding-top: 8px; font-size: 0.85rem; color: var(--mtt-muted); border-top: 1px solid var(--mtt-rule); }

  .message { padding: 8px 0; color: var(--mtt-muted); }
  .stale { margin-top: 8px; font-size: 0.8rem; font-weight: 600; color: var(--mtt-late); }
`;

/** Build an element. Text goes through textContent so feed data can't inject markup. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text).trim();
  return node;
}

function formatClock(value, format) {
  const match = /^(\d{1,2}):(\d{2})/.exec(value ?? "");
  if (format === "raw" || !match) return value ?? "";
  // GTFS allows hours past 24 for after-midnight trips.
  const hours = Number(match[1]) % 24;
  if (format === "24h") return `${String(hours).padStart(2, "0")}:${match[2]}`;
  return `${hours % 12 || 12}:${match[2]} ${hours < 12 ? "AM" : "PM"}`;
}

/** Whole minutes until a time. Rounded down, so "1" still means catchable. */
function minutesUntil(iso) {
  const time = new Date(iso).getTime();
  return Number.isNaN(time) ? null : Math.floor((time - Date.now()) / 60000);
}

function formatCountdown(minutes, short) {
  if (minutes === null) return "";
  if (minutes <= 0) return "Due";
  if (minutes < LONG_COUNTDOWN_MINUTES) return short ? `${minutes}m` : `in ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = String(minutes % 60);
  if (short) return `${hours}h ${rest.padStart(2, "0")}`;
  return minutes % 60 ? `in ${hours} hr ${rest} min` : `in ${hours} hr`;
}

/** The facts about a train's call at the station, independent of how they're shown. */
function describeTrip(trip, format) {
  const stop = trip.stops?.[0] ?? {};
  const delay = stop.delayedBy ?? 0;
  const live = stop.realtimeArrivalTime ?? stop.realtimeDepartureTime ?? null;
  const scheduled = formatClock(stop.arrivalTime ?? stop.departureTime, format);
  // Within a minute of schedule counts as on time, so don't show a slightly different time.
  const expected = delay !== 0 && live ? formatClock(live, format) : scheduled;
  let status = { text: "Scheduled", className: "muted" };
  if (delay > 0) status = { text: `+${delay} min late`, className: "late" };
  else if (delay < 0) status = { text: `${-delay} min early`, className: "good" };
  else if (live) status = { text: "On time", className: "good" };
  return {
    train: trip.train,
    favorite: Boolean(trip.favorite),
    headsign: trip.tripHeadsign ?? "",
    delayed: delay !== 0,
    scheduled,
    expected,
    status,
    minutes: minutesUntil(stop.expectedArrival),
    origin: trip.originName
      ? `leaves ${trip.originName} ${formatClock(trip.originDepartureTime, format)}`
      : "",
  };
}

class MetraTrainTrackerCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass?.states ?? {}).find(
      (id) => id.startsWith("sensor.") && hass.states[id].attributes?.filters?.stop,
    );
    return { entity: entity ?? "" };
  }

  static getConfigElement() {
    return document.createElement("metra-train-tracker-card-editor");
  }

  setConfig(config) {
    if (!config) throw new Error("metra-train-tracker-card: invalid configuration");
    for (const [option, choices] of Object.entries(CHOICES)) {
      if (config[option] !== undefined && !choices.includes(config[option])) {
        throw new Error(`metra-train-tracker-card: '${option}' must be one of ${choices.join(", ")}`);
      }
    }
    this._config = { ...DEFAULTS, ...config };
    this._stateObj = this._hass?.states[config.entity];
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      const style = el("style");
      style.textContent = STYLES;
      this._card = el("ha-card");
      this.shadowRoot.append(style, this._card);
    }
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // hass is reassigned on every state change in HA; only redraw for ours.
    const stateObj = hass?.states[this._config?.entity];
    if (stateObj === this._stateObj && this._rendered) return;
    this._stateObj = stateObj;
    this._render();
  }

  connectedCallback() {
    // Countdowns move on, and a stopped sensor fires no change, so redraw on a timer.
    this._refreshTimer = setInterval(() => this._render(), REFRESH_MS);
    this._render();
  }

  disconnectedCallback() {
    clearInterval(this._refreshTimer);
  }

  getCardSize() {
    if (this._config?.layout === "next_train") return 3;
    const trains = this._visibleTrips().trips.length;
    return this._config?.layout === "compact" ? 1 + trains : 2 + trains * 2;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  get _route() {
    return this._stateObj?.attributes?.routes?.[0] ?? null;
  }

  /** Apply the favorites and max_trains options. A pinned trip is kept separate. */
  _visibleTrips() {
    const attributes = this._stateObj?.attributes ?? {};
    const only = this._config?.favorites === "only";
    let trips = only ? attributes.favoriteTrips ?? [] : this._route?.trips ?? [];
    const limit = Number(this._config?.max_trains);
    if (limit > 0) trips = trips.slice(0, limit);
    const next = attributes.nextFavorite;
    const pinned =
      this._config?.pin_favorite && next && !trips.some((trip) => trip.tripId === next.tripId)
        ? next
        : null;
    return { trips, pinned };
  }

  _render() {
    if (!this._card || !this._config || !this._hass) return;
    this._rendered = true;
    this._card.replaceChildren(...this._renderContent());
  }

  _renderContent() {
    const stateObj = this._stateObj;
    const message = (text) => [el("div", "message", text)];
    if (!this._config.entity) return message("Choose a station's Next arrival sensor");
    if (!stateObj) return message(`Entity not found: ${this._config.entity}`);
    if (stateObj.state === "unavailable") return message("Metra schedule is unavailable");
    if (!Array.isArray(stateObj.attributes.routes)) return message("This sensor has no Metra schedule");

    const parts = [...this._renderTop(), ...this._renderTrains()];
    const stale = this._renderStale();
    if (stale) parts.push(stale);
    return parts;
  }

  _renderTop() {
    const alerts = this._route?.alerts ?? [];
    const mode = this._config.alerts;
    const chip = alerts.length > 0 && mode === "summary" ? this._renderAlertChip(alerts) : null;
    const parts = [];
    if (this._config.show_header) {
      const header = el("div", "header");
      const text = el("div", "header-text");
      text.append(el("div", "title", this._title()));
      const subtitle = this._subtitle();
      if (subtitle) text.append(el("div", "subtitle", subtitle));
      header.append(text);
      if (chip) header.append(chip);
      parts.push(header);
    } else if (chip) {
      // Without a header, alerts still need somewhere to appear.
      const row = el("div", "chip-row");
      row.append(chip);
      parts.push(row);
    }
    if (mode === "full" || (mode === "summary" && this._alertsOpen)) {
      parts.push(...alerts.map((alert) => this._renderAlert(alert)));
    }
    return parts;
  }

  _title() {
    if (this._config.title) return this._config.title;
    const { filters = {} } = this._stateObj.attributes;
    const line = this._route?.routeShortName ?? filters.route;
    const station = filters.stopName ?? filters.stop;
    return [line, station].filter(Boolean).join(" · ");
  }

  _subtitle() {
    const headsigns = new Set((this._route?.trips ?? []).map((trip) => trip.tripHeadsign));
    if (headsigns.size === 1) return `to ${[...headsigns][0]}`;
    const direction = this._stateObj.attributes.filters?.direction;
    return direction ? direction[0].toUpperCase() + direction.slice(1) : "";
  }

  _renderAlertChip(alerts) {
    const compact = this._config.layout !== "list";
    const count = compact ? alerts.length : `${alerts.length} ${alerts.length === 1 ? "alert" : "alerts"}`;
    const chip = el("button", "chip", `⚠ ${count}${this._alertsOpen ? " ▴" : ""}`);
    chip.type = "button";
    chip.setAttribute("aria-expanded", String(Boolean(this._alertsOpen)));
    chip.setAttribute("aria-label", `${alerts.length} service alerts`);
    chip.addEventListener("click", () => {
      this._alertsOpen = !this._alertsOpen;
      this._render();
    });
    return chip;
  }

  _renderAlert(alert) {
    const item = el("div", "alert");
    if (alert.header) item.append(el("div", "alert-header", alert.header));
    if (alert.description) item.append(el("div", "alert-description", alert.description));
    return item;
  }

  _renderTrains() {
    const { trips, pinned } = this._visibleTrips();
    const onlyFavorites = this._config.favorites === "only";
    if (trips.length === 0 && !pinned) {
      return [el("div", "message", onlyFavorites ? "No favorite trains left today" : "No upcoming trains")];
    }
    const format = this._config.time_format;
    const facts = trips.map((trip) => describeTrip(trip, format));
    const pin = pinned ? describeTrip(pinned, format) : null;
    if (this._config.layout === "next_train") return this._renderHero(facts, pin);
    const render = (trip) =>
      this._config.layout === "compact" ? this._renderCompactRow(trip) : this._renderRow(trip);
    const rows = facts.map(render);
    if (pin) rows.push(el("div", "pin-label", "★ Next favorite"), render(pin));
    const list = el("div", "");
    list.append(...rows);
    return [list];
  }

  _renderTrainBadge(trip, parent) {
    parent.append(el("span", "badge", `#${trip.train}`));
    if (trip.favorite && this._config.favorites !== "highlight") {
      const star = el("span", "star", "★");
      star.title = "Favorite train";
      parent.append(star);
    }
  }

  _renderTime(trip) {
    const time = el("div", "");
    const display = this._config.time_display;
    if (display === "scheduled") {
      time.append(el("span", "time", trip.scheduled));
    } else {
      time.append(el("span", "time", trip.expected));
      if (display === "both" && trip.delayed) time.append(el("span", "scheduled-time", `sch ${trip.scheduled}`));
      else if (display === "expected" && trip.delayed) time.append(el("span", "old-time", trip.scheduled));
    }
    return time;
  }

  _countdownText(trip, short) {
    const mode = this._config.countdown;
    if (mode === "off") return "";
    if (mode === "auto" && (trip.minutes === null || trip.minutes > AUTO_COUNTDOWN_MINUTES)) return "";
    return formatCountdown(trip.minutes, short);
  }

  _isHighlighted(trip) {
    return trip.favorite && this._config.favorites === "highlight";
  }

  _showHeadsign() {
    return new Set((this._route?.trips ?? []).map((trip) => trip.tripHeadsign)).size > 1;
  }

  _renderRow(trip) {
    const row = el("div", this._isHighlighted(trip) ? "row highlight" : "row");
    const badge = el("div", "");
    this._renderTrainBadge(trip, badge);

    const details = el("div", "details");
    details.append(this._renderTime(trip));
    const status = el("div", "status");
    status.append(el("span", trip.status.className, trip.status.text));
    const extras = [];
    if (this._showHeadsign()) extras.push(`to ${trip.headsign}`);
    if (this._config.show_origin && trip.origin) extras.push(trip.origin);
    if (extras.length) {
      const extra = el("span", "muted");
      // el() trims text, which would lose the leading space.
      extra.textContent = ` · ${extras.join(" · ")}`;
      status.append(extra);
    }
    details.append(status);

    row.append(badge, details, el("div", "countdown", this._countdownText(trip, false)));
    return row;
  }

  _renderCompactRow(trip) {
    const row = el("div", this._isHighlighted(trip) ? "compact-row highlight" : "compact-row");
    const badge = el("span", "");
    this._renderTrainBadge(trip, badge);
    const time = this._config.time_display === "scheduled" ? trip.scheduled : trip.expected;
    const delay = trip.delayed ? trip.status.text.replace(/ min (late|early)$/, "") : "";
    row.append(
      badge,
      el("span", "time", time),
      el("span", `compact-delay ${trip.status.className}`, delay),
      el("span", "countdown", this._countdownText(trip, true)),
    );
    return row;
  }

  _renderHero(facts, pin) {
    const [first, ...others] = facts.length ? facts : [pin];
    const following = others.slice(0, NEXT_TRAIN_FOLLOWING);
    if (pin && facts.length) following.push(pin);

    const big = this._countdownText(first, false).replace(/^in /, "");
    const hero = el("div", "");
    hero.append(el("div", "hero-time", big || first.expected));
    const info = el("div", "hero-info");
    const badge = el("span", "");
    this._renderTrainBadge(first, badge);
    info.append(badge);
    if (big) info.append(el("span", "time", first.expected));
    if (first.delayed) info.append(el("span", "old-time", first.scheduled));
    info.append(el("span", first.status.className, first.status.text));
    hero.append(info);
    if (this._config.show_origin && first.origin) hero.append(el("div", "status muted", first.origin));
    if (following.length) {
      const text = following
        .map((trip) => `#${trip.train}${trip.favorite ? " ★" : ""} ${trip.expected}`)
        .join("  ·  ");
      hero.append(el("div", "hero-then", `Then  ${text}`));
    }
    return [hero];
  }

  _renderStale() {
    // Times on this card are only trustworthy if the data is recent.
    const generatedAt = new Date(this._stateObj.attributes.generatedAt);
    if (!(Date.now() - generatedAt > STALE_AFTER_MS)) return null;
    const asOf = generatedAt.toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
      timeZone: SCHEDULE_TIME_ZONE,
    });
    return el("div", "stale", `Stale: data from ${asOf}`);
  }
}

const select = (name, options, required = true) => ({
  name,
  required,
  selector: {
    select: {
      mode: "dropdown",
      options: options.map(([value, label]) => ({ value, label })),
    },
  },
});

const EDITOR_SCHEMA = [
  {
    name: "entity",
    required: true,
    selector: { entity: { filter: { integration: "metra_train_tracker", domain: "sensor", device_class: "timestamp" } } },
  },
  select("layout", [
    ["list", "List"],
    ["compact", "Compact"],
    ["next_train", "Next train"],
  ]),
  select("time_display", [
    ["expected", "Expected, with the original time crossed out if late"],
    ["scheduled", "Scheduled"],
    ["both", "Both"],
  ]),
  select("time_format", [
    ["12h", "12-hour (9:04 AM)"],
    ["24h", "24-hour (09:04)"],
    ["raw", "With seconds (09:04:00)"],
  ]),
  select("countdown", [
    ["minutes", "Always"],
    ["auto", `Within ${AUTO_COUNTDOWN_MINUTES} minutes`],
    ["off", "Off"],
  ]),
  select("alerts", [
    ["summary", "Summary you can tap to open"],
    ["full", "Always open"],
    ["hidden", "Hidden"],
  ]),
  select("favorites", [
    ["highlight", "Highlight the row"],
    ["mark", "Mark with a star"],
    ["only", "Show only favorites"],
  ]),
  { name: "pin_favorite", selector: { boolean: {} } },
  { name: "max_trains", selector: { number: { min: 1, max: 10, mode: "box" } } },
  { name: "title", selector: { text: {} } },
  { name: "show_header", selector: { boolean: {} } },
  { name: "show_origin", selector: { boolean: {} } },
];

const EDITOR_LABELS = {
  entity: "Station's Next arrival sensor",
  layout: "Layout",
  time_display: "Time shown",
  time_format: "Time format",
  countdown: "Countdown",
  alerts: "Service alerts",
  favorites: "Favorite trains",
  pin_favorite: "Always show next favorite",
  max_trains: "Most trains to show",
  title: "Title",
  show_header: "Show header",
  show_origin: "Show where each train starts",
};

const EDITOR_HELPERS = {
  max_trains: "Up to the station's Trains to show setting. Leave blank to show them all.",
  title: "Leave blank to use the line and station.",
};

const EDITOR_KEYS = new Set(EDITOR_SCHEMA.map((field) => field.name));

class MetraTrainTrackerCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  _update() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.schema = EDITOR_SCHEMA;
      this._form.computeLabel = (field) => EDITOR_LABELS[field.name] ?? field.name;
      this._form.computeHelper = (field) => EDITOR_HELPERS[field.name];
      this._form.addEventListener("value-changed", (event) => {
        event.stopPropagation();
        // Cleared fields are missing from the value, so replace rather than merge them.
        const kept = Object.fromEntries(
          Object.entries(this._config).filter(([key]) => !EDITOR_KEYS.has(key)),
        );
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: { ...kept, ...event.detail.value } },
            bubbles: true,
            composed: true,
          }),
        );
      });
      this.append(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = { ...DEFAULTS, ...this._config };
  }
}

// The integration loads this file early, and the frontend later replaces
// window.customElements with a polyfill that can't see anything defined before
// that. So wait for the app, whose definition comes after the replacement.
customElements.whenDefined("home-assistant").then(() => {
  // An older manually installed copy may already have claimed these names.
  if (!customElements.get("metra-train-tracker-card")) customElements.define("metra-train-tracker-card", MetraTrainTrackerCard);
  if (!customElements.get("metra-train-tracker-card-editor")) customElements.define("metra-train-tracker-card-editor", MetraTrainTrackerCardEditor);
});

window.customCards = window.customCards || [];
window.customCards.push({
  type: "metra-train-tracker-card",
  name: "Metra Train Tracker",
  description: "Upcoming Metra trains, delays and alerts for a station.",
  documentationURL: "https://github.com/adamjeffery/metra-ha",
  preview: true,
});
