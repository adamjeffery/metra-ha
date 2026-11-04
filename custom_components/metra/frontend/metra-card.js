/**
 * Metra schedule card, served automatically by the Metra integration.
 *
 * Displays the schedule held by a station's "Next arrival" sensor.
 *
 *   type: custom:metra-card
 *   entity: sensor.up_w_lombard_inbound_next_arrival
 *   time_format: 12h   # optional: 12h (default), 24h or raw
 */

const STALE_AFTER_MS = 5 * 60 * 1000;
const STALE_CHECK_MS = 60 * 1000;
const SCHEDULE_TIME_ZONE = "America/Chicago";
const TIME_FORMATS = ["12h", "24h", "raw"];

const STYLES = `
  :host {
    --metra-fg: var(--primary-text-color, #000);
    --metra-bg: var(--ha-card-background, var(--card-background-color, #fff));
    --metra-muted: var(--secondary-text-color, #666);
    --metra-rule: var(--divider-color, #ccc);
  }
  ha-card {
    display: flex;
    flex-direction: column;
    height: 100%;
    box-sizing: border-box;
    padding: 16px;
    color: var(--metra-fg);
    container-type: inline-size;
  }
  .routes { display: flex; flex-wrap: wrap; gap: 16px; flex: 1 1 auto; }
  .route { flex: 1 1 280px; min-width: 0; }
  .route-title { font-size: 1.25rem; font-weight: 600; line-height: 1.3; margin-bottom: 8px; }

  /* Left bar stands in for the TRMNL item "meta" column. */
  .item { display: flex; gap: 10px; padding-top: 8px; }
  .meta { flex: 0 0 6px; border-radius: 1px; background: var(--metra-rule); }
  .alert .meta { background: var(--metra-fg); }
  .content { flex: 1 1 auto; min-width: 0; padding-bottom: 4px; }

  .alert-header { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .alert-description {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    overflow: hidden;
    font-size: 0.875rem;
    line-height: 1.35;
    color: var(--metra-muted);
  }

  .trip-header { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; margin-bottom: 4px; }
  .train { font-weight: 700; }
  .headsign { font-size: 0.875rem; color: var(--metra-muted); }
  .inverted {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--metra-fg);
    color: var(--metra-bg);
    white-space: nowrap;
  }

  /* Each trip is its own table, so fixed column widths keep times aligned between trips. */
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  td { box-sizing: border-box; padding: 2px 8px 2px 0; white-space: nowrap; }
  .stop-name { overflow: hidden; text-overflow: ellipsis; font-weight: 500; }
  .separator { width: 1.5em; color: var(--metra-muted); }
  .time { width: 7em; }
  .delay-cell { width: 8em; padding-right: 0; text-align: right; }
  .delay { font-size: 0.8125rem; }

  /* Too narrow for four columns: put the delay badge on its own line. */
  @container (max-width: 380px) {
    tr { display: flex; flex-wrap: wrap; align-items: baseline; }
    .stop-name { flex: 1 1 0; min-width: 0; }
    .separator, .time { flex: 0 0 auto; }
    .delay-cell { flex: 1 0 100%; width: auto; }
    .delay-cell:empty { display: none; }
  }

  .message { padding: 8px 0; color: var(--metra-muted); }

  .title-bar { display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 0.8125rem; }
  .brand { font-weight: 700; }
  .instance { color: var(--metra-muted); }
  .stale { margin-left: auto; font-weight: 600; white-space: nowrap; }
`;

/** Build an element. Text goes through textContent so feed data can't inject markup. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text).trim();
  return node;
}

function formatScheduleTime(value, format) {
  const match = /^(\d{1,2}):(\d{2})/.exec(value ?? "");
  if (format === "raw" || !match) return value ?? "";
  // GTFS allows hours past 24 for after-midnight trips.
  const hours = Number(match[1]) % 24;
  if (format === "24h") return `${String(hours).padStart(2, "0")}:${match[2]}`;
  return `${hours % 12 || 12}:${match[2]} ${hours < 12 ? "AM" : "PM"}`;
}

class MetraCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass?.states ?? {}).find(
      (id) => id.startsWith("sensor.") && hass.states[id].attributes?.filters?.stop,
    );
    return { entity: entity ?? "" };
  }

  static getConfigElement() {
    return document.createElement("metra-card-editor");
  }

  setConfig(config) {
    if (!config) throw new Error("metra-card: invalid configuration");
    if (config.time_format && !TIME_FORMATS.includes(config.time_format)) {
      throw new Error(`metra-card: 'time_format' must be one of ${TIME_FORMATS.join(", ")}`);
    }
    this._config = { time_format: "12h", ...config };
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
    // A sensor that has stopped updating fires no change, so check on a timer.
    this._staleTimer = setInterval(() => this._render(), STALE_CHECK_MS);
  }

  disconnectedCallback() {
    clearInterval(this._staleTimer);
  }

  getCardSize() {
    const trips = (this._stateObj?.attributes?.routes ?? []).reduce(
      (total, route) => total + (route.trips?.length ?? 0),
      0,
    );
    return 2 + trips * 2;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _render() {
    if (!this._card || !this._config || !this._hass) return;
    this._rendered = true;
    const body = this._renderBody();
    this._card.replaceChildren(body, this._renderTitleBar());
  }

  _renderBody() {
    const stateObj = this._stateObj;
    if (!this._config.entity) return el("div", "message", "Choose a station's Next arrival sensor");
    if (!stateObj) return el("div", "message", `Entity not found: ${this._config.entity}`);
    if (stateObj.state === "unavailable") return el("div", "message", "Metra schedule is unavailable");

    const { routes } = stateObj.attributes;
    if (!Array.isArray(routes)) return el("div", "message", "This sensor has no Metra schedule");
    if (routes.length === 0) return el("div", "message", "No upcoming trains");

    const container = el("div", "routes");
    container.append(...routes.map((route) => this._renderRoute(route)));
    return container;
  }

  _renderRoute(route) {
    const section = el("section", "route");
    section.append(el("div", "route-title", `${route.routeLongName?.trim()} (${route.routeShortName?.trim()})`));
    for (const alert of route.alerts ?? []) section.append(this._renderAlert(alert));
    const trips = route.trips ?? [];
    if (trips.length === 0) section.append(el("div", "message", "No upcoming trains"));
    for (const trip of trips) section.append(this._renderTrip(trip));
    return section;
  }

  _renderAlert(alert) {
    const item = el("div", "item alert");
    const content = el("div", "content");
    if (alert.header) {
      const header = el("div", "alert-header", alert.header);
      header.title = alert.header;
      content.append(header);
    }
    if (alert.description) content.append(el("div", "alert-description", alert.description));
    item.append(el("div", "meta"), content);
    return item;
  }

  _renderTrip(trip) {
    const header = el("div", "trip-header");
    header.append(el("span", "train inverted", `#${trip.train}`), el("span", "headsign", `to ${trip.tripHeadsign ?? ""}`));

    const table = el("table");
    const stops = [...(trip.stops ?? [])].sort((a, b) => a.stopSequence - b.stopSequence);
    table.append(...stops.map((stop) => this._renderStop(stop)));

    const content = el("div", "content");
    content.append(header, table);
    const item = el("article", "item");
    item.append(el("div", "meta"), content);
    return item;
  }

  _renderStop(stop) {
    const row = el("tr");
    const name = el("td", "stop-name", stop.stopName);
    name.title = stop.stopName ?? "";
    const delayCell = el("td", "delay-cell");
    if (stop.delayedBy > 0) {
      const unit = stop.delayedBy === 1 ? "min" : "mins";
      delayCell.append(el("span", "delay inverted", `Delayed ${stop.delayedBy} ${unit}`));
    }
    row.append(
      name,
      el("td", "separator", "-"),
      el("td", "time", `Sch ${formatScheduleTime(stop.arrivalTime, this._config.time_format)}`),
      delayCell,
    );
    return row;
  }

  _renderTitleBar() {
    const bar = el("div", "title-bar");
    bar.append(el("span", "brand", "Metra"));
    const attributes = this._stateObj?.attributes ?? {};
    bar.append(el("span", "instance", attributes.filters?.route?.toUpperCase() ?? "Live"));

    // Times on this card are only trustworthy if the data is recent.
    const generatedAt = new Date(attributes.generatedAt);
    if (Date.now() - generatedAt > STALE_AFTER_MS) {
      const asOf = generatedAt.toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit",
        timeZone: SCHEDULE_TIME_ZONE,
      });
      bar.append(el("span", "stale", `Stale: data from ${asOf}`));
    }
    return bar;
  }
}

const EDITOR_SCHEMA = [
  {
    name: "entity",
    required: true,
    selector: { entity: { filter: { integration: "metra", domain: "sensor", device_class: "timestamp" } } },
  },
  {
    name: "time_format",
    required: true,
    selector: {
      select: {
        mode: "dropdown",
        options: [
          { value: "12h", label: "12-hour (9:04 AM)" },
          { value: "24h", label: "24-hour (09:04)" },
          { value: "raw", label: "With seconds (09:04:00)" },
        ],
      },
    },
  },
];

const EDITOR_LABELS = { entity: "Station's Next arrival sensor", time_format: "Time format" };

class MetraCardEditor extends HTMLElement {
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
      this._form.addEventListener("value-changed", (event) => {
        event.stopPropagation();
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: { ...this._config, ...event.detail.value } },
            bubbles: true,
            composed: true,
          }),
        );
      });
      this.append(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = { time_format: "12h", ...this._config };
  }
}

// The integration loads this file early, and the frontend later replaces
// window.customElements with a polyfill that can't see anything defined before
// that. So wait for the app, whose definition comes after the replacement.
customElements.whenDefined("home-assistant").then(() => {
  // An older manually installed copy may already have claimed these names.
  if (!customElements.get("metra-card")) customElements.define("metra-card", MetraCard);
  if (!customElements.get("metra-card-editor")) customElements.define("metra-card-editor", MetraCardEditor);
});

window.customCards = window.customCards || [];
window.customCards.push({
  type: "metra-card",
  name: "Metra Schedule",
  description: "Upcoming Metra trains, delays and alerts for a station.",
  documentationURL: "https://github.com/adamjeffery/metra-ha",
  preview: true,
});
