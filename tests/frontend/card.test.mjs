import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { Window } from "happy-dom";

const source = await readFile(new URL("../../custom_components/hikvision_next/www/hikvision-camera-card.js", import.meta.url), "utf8");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

async function setup(t, config = {}, options = {}) {
  const window = new Window({ url: "http://homeassistant.local:8123" });
  t.after(() => window.happyDOM.abort());
  const calls = [];
  const players = [];
  const disconnected = [];
  class NativeCameraStream extends window.HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      const rtc = window.document.createElement("ha-web-rtc-player");
      rtc.attachShadow({ mode: "open" });
      const video = window.document.createElement("video");
      video.play = async () => { video.playedByCard = true; };
      rtc.shadowRoot.append(video); this.shadowRoot.append(rtc);
    }
    connectedCallback() { players.push(this); }
    disconnectedCallback() { disconnected.push(this); }
  }
  if (!options.lazy) window.customElements.define("ha-camera-stream", NativeCameraStream);
  else window.loadCardHelpers = async () => ({
    createCardElement(config) {
      assert.equal(config.type, "picture-entity");
      window.customElements.define("ha-camera-stream", NativeCameraStream);
      return window.document.createElement("div");
    },
  });
  window.eval(source);
  const state = (id, channel = 1, type = 1) => ({
    entity_id: id, state: "idle", attributes: {
      friendly_name: "Front door", hikvision_next: {
        config_entry_id: "entry-one", channel_id: channel, stream_id: channel * 100 + type,
        stream_type: type, ptz: true, intercom: true,
      },
    },
  });
  const hass = {
    language: "en", states: {
      "camera.main": state("camera.main"), "camera.sub": state("camera.sub", 1, 2),
      "camera.other": state("camera.other", 2, 1),
    },
    callService: async (domain, action, data) => { calls.push({ domain, action, data: structuredClone(data) }); },
  };
  const card = window.document.createElement("hikvision-camera-card");
  card.setConfig({ entity: "camera.main", ...config });
  card.hass = hass;
  window.document.body.append(card);
  await tick();
  t.after(() => card.remove());
  const button = (key) => card.shadowRoot.querySelector(`[data-move="${key}"]`);
  const press = (key) => {
    const target = button(key);
    target.setPointerCapture = () => {};
    target.dispatchEvent(new window.PointerEvent("pointerdown", { button: 0, pointerId: 1 }));
    return target;
  };
  return { window, card, hass, calls, players, disconnected, button, press };
}

test("auto registration, lazy player loading and native player properties", async (t) => {
  const { window, card, hass, players } = await setup(t, {}, { lazy: true });
  assert.equal(window.customCards[0].type, "hikvision-camera-card");
  assert.equal(window.customCards[0].preview, true);
  window.eval(source);
  assert.equal(window.customCards.length, 1);
  assert.equal(players.length, 1);
  assert.equal(players[0].stateObj, hass.states["camera.main"]);
  assert.equal(players[0].hass, hass);
  assert.equal(players[0].muted, true);
  assert.equal(players[0].controls, false);
  assert.equal(card.constructor.getStubConfig(hass).entity, "camera.main");
  assert.ok(card.constructor.getConfigElement().localName.endsWith("-editor"));
});

test("HA state updates preserve the active player; unrelated channels stay out of stream picker", async (t) => {
  const { card, hass, players } = await setup(t);
  const select = card.shadowRoot.querySelector("select");
  assert.deepEqual([...select.options].map((option) => option.value), ["camera.main", "camera.sub"]);
  card.hass = { ...hass, states: { ...hass.states, "sensor.weather": { state: "sunny" } } };
  await tick();
  assert.equal(players.length, 1);
});

test("switching streams closes old player and preserves control routing", async (t) => {
  const { card, window, players, disconnected, calls, press } = await setup(t);
  const select = card.shadowRoot.querySelector("select");
  select.value = "camera.sub"; select.dispatchEvent(new window.Event("change"));
  await tick();
  assert.equal(disconnected.length, 1);
  assert.equal(players[1].stateObj.entity_id, "camera.sub");
  const target = press("right");
  target.dispatchEvent(new window.PointerEvent("pointerup"));
  await tick();
  assert.deepEqual(calls.map((call) => call.action), ["ptz_move", "ptz_stop"]);
  assert.deepEqual(calls[0].data, { config_entry_id: "entry-one", channel_id: 1, pan: 40 });
});

test("quick PTZ release queues stop after an in-flight move", async (t) => {
  const { card, hass, window, calls, press } = await setup(t, { ptz_speed: 25 });
  const pending = deferred();
  hass.callService = async (domain, action, data) => {
    calls.push({ domain, action, data });
    if (action === "ptz_move") await pending.promise;
  };
  const target = press("left");
  await tick();
  target.dispatchEvent(new window.PointerEvent("pointerup"));
  await tick();
  assert.deepEqual(calls.map((call) => call.action), ["ptz_move"]);
  pending.resolve();
  await card._queue;
  assert.deepEqual(calls.map((call) => call.action), ["ptz_move", "ptz_stop"]);
  assert.equal(calls[0].data.pan, -25);
});

for (const [direction, expected] of [
  ["up", { tilt: 40 }], ["down", { tilt: -40 }],
  ["left", { pan: -40 }], ["right", { pan: 40 }],
  ["zoom_in", { zoom: 40 }], ["zoom_out", { zoom: -40 }],
]) {
  test(`card ${direction} sends the matching velocity`, async (t) => {
    const { window, card, calls, press } = await setup(t);
    const button = press(direction);
    button.dispatchEvent(new window.PointerEvent("pointerup"));
    await card._queue;
    assert.deepEqual(calls[0], {
      domain: "hikvision_next", action: "ptz_move",
      data: { config_entry_id: "entry-one", channel_id: 1, ...expected },
    });
    assert.equal(calls[1].action, "ptz_stop");
  });
}

for (const reason of ["pointercancel", "lostpointercapture", "blur", "disconnect", "switch", "unavailable"]) {
  test(`PTZ stops on ${reason}`, async (t) => {
    const { window, card, hass, calls, press } = await setup(t);
    const target = press("up");
    if (reason === "disconnect") card.remove();
    else if (reason === "switch") card._switchStream("camera.sub");
    else if (reason === "unavailable") {
      hass.states["camera.main"] = { ...hass.states["camera.main"], state: "unavailable" };
      card.hass = hass;
    } else target.dispatchEvent(new window.Event(reason));
    await card._queue;
    assert.deepEqual(calls.map((call) => call.action), ["ptz_move", "ptz_stop"]);
    assert.equal(calls[1].data.channel_id, 1);
  });
}

test("PTZ keyboard controls and stop button", async (t) => {
  const { window, card, calls, button } = await setup(t);
  const zoom = button("zoom_in");
  zoom.dispatchEvent(new window.KeyboardEvent("keydown", { key: " " }));
  zoom.dispatchEvent(new window.KeyboardEvent("keydown", { key: " ", repeat: true }));
  zoom.dispatchEvent(new window.KeyboardEvent("keyup", { key: " " }));
  await card._queue;
  assert.equal(calls[0].data.zoom, 40);
  assert.deepEqual(calls.map((call) => call.action), ["ptz_move", "ptz_stop"]);
  button("stop").click(); await card._queue;
  assert.equal(calls.at(-1).action, "ptz_stop");
});

test("capability controls stay hidden and no calls occur for non-PTZ cameras", async (t) => {
  const { card, hass, calls } = await setup(t);
  hass.states["camera.main"].attributes.hikvision_next.ptz = false;
  hass.states["camera.main"].attributes.hikvision_next.intercom = false;
  card.hass = hass;
  assert.equal(card.shadowRoot.querySelector(".ptz").hidden, true);
  assert.equal(card.shadowRoot.querySelector(".intercom").hidden, true);
  card._startMotion({ pan: 1 }); await tick();
  assert.equal(calls.length, 0);
});

test("first unmute rebuilds HA's muted player; subsequent mute toggles reuse the audio session", async (t) => {
  const { card, players, disconnected } = await setup(t);
  card._setMuted(false); await tick();
  assert.equal(disconnected.length, 1);
  assert.equal(players.length, 2);
  assert.equal(players[1].muted, false);
  card._setMuted(true); card._setMuted(false); await tick();
  assert.equal(players.length, 2);
  assert.equal(players[1].muted, false);
});

test("volume and full screen use the current native video; playback resumes on user gesture", async (t) => {
  const { card, window } = await setup(t, { muted: false });
  const input = card.shadowRoot.querySelector(".volume");
  input.value = "0.4"; input.dispatchEvent(new window.Event("input"));
  const video = card._videos()[0];
  assert.equal(video.volume, 0.4);
  await card._playVideo();
  assert.equal(video.playedByCard, true);
  let full = false;
  card.shadowRoot.querySelector(".viewport").requestFullscreen = async () => { full = true; };
  await card._enterFullscreen(); assert.equal(full, true);
});

test("intercom calls use config entry and show failures without injecting HTML", async (t) => {
  const { card, hass, calls } = await setup(t);
  await card._intercomAction("answer");
  assert.deepEqual(calls[0], { domain: "hikvision_next", action: "intercom_answer", data: { config_entry_id: "entry-one" } });
  hass.callService = async () => { throw new Error("<img src=x onerror=alert(1)>"); };
  await card._intercomAction("reject");
  const error = card.shadowRoot.querySelector(".error");
  assert.equal(error.hidden, false);
  assert.match(error.textContent, /<img/);
  assert.equal(error.querySelector("img"), null);
  assert.equal(card.shadowRoot.querySelector(".intercom button").disabled, false);
});

test("disconnect invalidates a pending player mount and reconnect starts only one player", async (t) => {
  const { card, window, players } = await setup(t);
  card._removePlayer();
  card._mountPlayer(); card.remove();
  await tick();
  assert.equal(card.shadowRoot.querySelector("ha-camera-stream"), null);
  window.document.body.append(card); await tick();
  assert.equal(players.length, 2);
});

test("visual editor emits saved config and never places camera names into HTML", async (t) => {
  const { window, hass } = await setup(t);
  const editor = window.document.createElement("hikvision-camera-card-editor");
  hass.language = "zh-Hans";
  hass.states["camera.main"].attributes.friendly_name = "<script>bad()</script>";
  editor.setConfig({ type: "custom:hikvision-camera-card", entity: "camera.main" });
  editor.hass = hass;
  window.document.body.append(editor);
  let saved;
  editor.addEventListener("config-changed", (event) => { saved = event.detail.config; });
  const select = editor.shadowRoot.querySelector('[data-field="sub_stream"]');
  select.value = "camera.sub"; select.dispatchEvent(new window.Event("change"));
  assert.equal(saved.sub_stream, "camera.sub");
  assert.equal(saved.entity, "camera.main");
  assert.equal(editor.shadowRoot.querySelector("script"), null);
  assert.match(editor.shadowRoot.textContent, /摄像头/);
});

test("invalid card configuration is rejected", async (t) => {
  const { card } = await setup(t);
  for (const config of [{}, { entity: "switch.foo" }, { entity: "camera.main", sub_stream: "light.foo" }, { entity: "camera.main", ptz_speed: 0 }, { entity: "camera.main", muted: "false" }]) {
    assert.throws(() => card.setConfig(config));
  }
});

async function setupControlPage(t, search = "?entry=entry-one", prepare) {
  const context = await setup(t);
  const { window, hass, players, disconnected } = context;
  context.card.remove(); players.length = 0; disconnected.length = 0;
  window.history.replaceState({}, "", `/hikvision-next-control/0${search}`);
  hass.entities = { "camera.main": { device_id: "device-one" }, "camera.other": { device_id: "device-two" } };
  if (prepare) prepare(hass);
  const page = window.document.createElement("hikvision-camera-control");
  page.setConfig({}); page.hass = hass;
  window.document.body.append(page);
  t.after(() => page.remove());
  await tick();
  return { ...context, page };
}

test("device control entry selects the requested channel and links back to its device", async (t) => {
  const { page, players } = await setupControlPage(t, "?entry=entry-one&channel=2");
  const card = page.shadowRoot.querySelector("hikvision-camera-card");
  assert.equal(card._config.entity, "camera.other");
  assert.equal(players.length, 1);
  assert.equal(page.shadowRoot.querySelector("select").options.length, 2);
  assert.match(page.shadowRoot.querySelector("a").href, /\/config\/devices\/device\/device-two$/);
});

test("NVR entry groups streams by channel and excludes other config entries", async (t) => {
  const { page, players, hass } = await setupControlPage(t, "?entry=entry-one", (hass) => {
    const state = structuredClone(hass.states["camera.other"]);
    state.entity_id = "camera.foreign";
    state.attributes.hikvision_next.config_entry_id = "entry-two";
    hass.states[state.entity_id] = state;
  });
  const values = [...page.shadowRoot.querySelector("select").options].map((option) => option.value);
  assert.deepEqual(values, ["entry-one:1", "entry-one:2"]);
  assert.equal(players.length, 1);
  assert.equal(page.shadowRoot.querySelector("hikvision-camera-card")._config.entity, "camera.main");
  page.hass = { ...hass }; await tick();
  assert.equal(players.length, 1);
});

test("control page channel switch closes the previous stream and leaves only one active card", async (t) => {
  const { page, window, players, disconnected } = await setupControlPage(t);
  const select = page.shadowRoot.querySelector("select");
  select.value = "entry-one:2"; select.dispatchEvent(new window.Event("change"));
  await tick();
  assert.equal(players.length, 2);
  assert.equal(disconnected.length, 1);
  assert.equal(page.shadowRoot.querySelectorAll("hikvision-camera-card").length, 1);
  assert.equal(page.shadowRoot.querySelector("hikvision-camera-card")._config.entity, "camera.other");
});

test("control page waits for states on a direct page load", async (t) => {
  let states;
  const { page, hass, players } = await setupControlPage(t, "?entry=entry-one&channel=2", (hass) => {
    states = hass.states; hass.states = {};
  });
  assert.equal(players.length, 0);
  hass.states = states; page.hass = hass; await tick();
  assert.equal(players.length, 1);
  assert.equal(page.shadowRoot.querySelector("hikvision-camera-card")._config.entity, "camera.other");
});

for (const search of ["?entry=missing", "?entry=entry-one&channel=999"]) {
  test(`unknown control target does not open another camera: ${search}`, async (t) => {
    const { page, players } = await setupControlPage(t, search);
    assert.equal(players.length, 0);
    assert.equal(page.shadowRoot.querySelector("hikvision-camera-card"), null);
    assert.equal(page.shadowRoot.querySelector(".message").hidden, false);
  });
}

test("leaving the control page releases its card and cached state updates do not reopen it", async (t) => {
  const { page, window, hass, players, disconnected } = await setupControlPage(t);
  page.remove(); page.hass = { ...hass }; await tick();
  assert.equal(disconnected.length, 1);
  assert.equal(page.shadowRoot.querySelector("hikvision-camera-card"), null);
  assert.equal(players.length, 1);
  window.document.body.append(page); await tick();
  assert.equal(players.length, 2);
});

test("control page follows HA route changes and cleans up a superseded channel", async (t) => {
  const { page, window, players, disconnected } = await setupControlPage(t, "?entry=entry-one&channel=1");
  window.history.replaceState({}, "", "/hikvision-next-control/0?entry=entry-one&channel=2");
  window.dispatchEvent(new window.Event("location-changed")); await tick();
  assert.equal(page.shadowRoot.querySelector("hikvision-camera-card")._config.entity, "camera.other");
  assert.equal(players.length, 2);
  assert.equal(disconnected.length, 1);
});

test("device control can open an enabled sub-stream when its main entity is disabled", async (t) => {
  const { page } = await setupControlPage(t, "?entry=entry-one&channel=1", (hass) => { delete hass.states["camera.main"]; });
  assert.equal(page.shadowRoot.querySelector("hikvision-camera-card")._config.entity, "camera.sub");
});
