/* Bundled dashboard card. No CDN, credentials, or separate signaling server. */
(() => {
  const TYPE = "hikvision-camera-card";
  const TEXT = {
    en: {
      camera: "Camera", name: "Name", sub_stream: "Optional sub-stream", show_ptz: "Show PTZ controls",
      show_intercom: "Show intercom controls", muted: "Start muted", ptz_speed: "PTZ speed (1–100)",
      missing: "Camera is unavailable", play: "Play / reconnect", sound: "Sound", mute: "Mute",
      volume: "Volume", fullscreen: "Fullscreen", main: "Main stream", sub: "Sub-stream", stream: "Stream",
      up: "Tilt up", down: "Tilt down", left: "Pan left", right: "Pan right", stop: "Stop PTZ",
      zoom_in: "Zoom in", zoom_out: "Zoom out", answer: "Answer", reject: "Reject", hangup: "Hang up",
      speed: "PTZ", loading: "Loading player…", player_error: "Cannot load HA camera player. Reload this page to retry.",
      description: "Live video, stream selection, PTZ and intercom controls",
      choose_camera: "Camera", control_page: "Camera controls", device_details: "Device details",
      no_camera: "No enabled camera found for this device/channel. Enable its camera entity in device settings.",
    },
    zh: {
      camera: "摄像头", name: "名称", sub_stream: "子码流（可选）", show_ptz: "显示云台控制",
      show_intercom: "显示门口机控制", muted: "默认静音", ptz_speed: "云台速度（1–100）",
      missing: "摄像头不可用", play: "播放／重新连接", sound: "开启声音", mute: "静音",
      volume: "音量", fullscreen: "全屏", main: "主码流", sub: "子码流", stream: "码流",
      up: "向上", down: "向下", left: "向左", right: "向右", stop: "停止云台",
      zoom_in: "放大", zoom_out: "缩小", answer: "接听", reject: "拒接", hangup: "挂断",
      speed: "云台", loading: "正在加载播放器…", player_error: "无法加载 HA 摄像头播放器，请刷新页面重试。",
      description: "实时视频、码流切换、云台和门口机控制",
      choose_camera: "选择摄像头", control_page: "摄像头控制", device_details: "设备详情",
      no_camera: "未找到该设备／通道已启用的摄像头，请先在设备设置中启用摄像头实体。",
    },
  };
  const text = (hass, key) => TEXT[hass?.language?.startsWith("zh") ? "zh" : "en"][key] || key;
  const metadata = (state) => state?.attributes?.hikvision_next;
  const cameraId = (value) => typeof value === "string" && /^camera\.[a-z0-9_]+$/.test(value);
  const fire = (element, name, detail) => element.dispatchEvent(new CustomEvent(name, { detail, bubbles: true, composed: true }));
  let playerReady;
  function loadPlayer(entity) {
    if (customElements.get("ha-camera-stream")) return Promise.resolve();
    if (!playerReady) {
      playerReady = (async () => {
        if (!window.loadCardHelpers) throw new Error("HA card helpers unavailable");
        const helpers = await window.loadCardHelpers();
        // Request HA's lazy camera module without mounting another player.
        helpers.createCardElement({ type: "picture-entity", entity, camera_view: "live" });
        let timer;
        try {
          await Promise.race([
            customElements.whenDefined("ha-camera-stream"),
            new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Player load timeout")), 15000); }),
          ]);
        } finally { clearTimeout(timer); }
      })().catch((error) => { playerReady = undefined; throw error; });
    }
    return playerReady;
  }

  class HikvisionCameraCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._queue = Promise.resolve();
      this._volume = 1;
      this._generation = 0;
      this._stopOnBlur = () => this._stopMotion();
      this._visibility = () => { if (document.hidden) this._stopMotion(); };
    }

    setConfig(config) {
      if (!cameraId(config.entity)) throw new Error("entity must be a camera entity_id");
      if (config.sub_stream && !cameraId(config.sub_stream)) throw new Error("sub_stream must be a camera entity_id");
      if (config.name !== undefined && typeof config.name !== "string") throw new Error("name must be a string");
      for (const key of ["muted", "show_ptz", "show_intercom"]) {
        if (config[key] !== undefined && typeof config[key] !== "boolean") throw new Error(`${key} must be boolean`);
      }
      if (config.ptz_speed !== undefined && (!Number.isInteger(config.ptz_speed) || config.ptz_speed < 1 || config.ptz_speed > 100)) {
        throw new Error("ptz_speed must be an integer from 1 to 100");
      }
      this._stopMotion();
      this._config = { muted: true, show_ptz: true, show_intercom: true, ptz_speed: 40, ...config };
      this._entity = config.entity;
      this._muted = this._config.muted;
      this._build();
      this._update();
    }

    set hass(hass) { this._hass = hass; this._update(); }
    get hass() { return this._hass; }
    getCardSize() { return 6; }
    getGridOptions() { return { columns: 12, min_columns: 6 }; }
    static getConfigElement() { return document.createElement(`${TYPE}-editor`); }
    static getStubConfig(hass) {
      const entity = Object.values(hass.states).find((state) => cameraId(state.entity_id) && metadata(state));
      return { entity: entity?.entity_id || "" };
    }

    connectedCallback() {
      window.addEventListener("blur", this._stopOnBlur);
      window.addEventListener("pagehide", this._stopOnBlur);
      document.addEventListener("visibilitychange", this._visibility);
      this._update();
    }

    disconnectedCallback() {
      this._stopMotion();
      window.removeEventListener("blur", this._stopOnBlur);
      window.removeEventListener("pagehide", this._stopOnBlur);
      document.removeEventListener("visibilitychange", this._visibility);
      this._removePlayer();
    }

    _button(label, callback, className = "") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = label;
      button.addEventListener("click", callback);
      return button;
    }

    _build() {
      this._removePlayer();
      this.shadowRoot.innerHTML = `
        <style>
          :host { display:block; height:100%; --control-size:42px; }
          ha-card { overflow:hidden; height:100%; background:var(--ha-card-background,var(--card-background-color,#fff)); }
          header { display:flex; align-items:center; gap:10px; padding:14px 16px; }
          h2 { margin:0; flex:1; font-size:17px; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .brand { font-size:10px; letter-spacing:1.5px; color:var(--secondary-text-color); }
          .viewport { background:#101419; position:relative; min-height:140px; aspect-ratio:16/9; display:grid; place-items:center; }
          .viewport ha-camera-stream { width:100%; position:absolute; inset:0; height:100%; }
          .placeholder { color:#cbd3dc; padding:24px; text-align:center; }
          .toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:8px; padding:10px 12px; }
          button, select { font:inherit; color:var(--primary-text-color); border:1px solid var(--divider-color,#ddd); background:var(--secondary-background-color,#fafafa); border-radius:9px; min-height:var(--control-size); padding:6px 12px; }
          button { cursor:pointer; display:grid; place-items:center; } button:hover { background:var(--divider-color,#eee); }
          ha-icon { --mdc-icon-size:22px; }
          button:focus-visible, select:focus-visible, input:focus-visible { outline:2px solid var(--primary-color); outline-offset:2px; }
          button:disabled { opacity:.4; cursor:default; } [hidden] { display:none !important; }
          .toolbar select { flex:1; min-width:100px; max-width:190px; }
          .volume { width:72px; accent-color:var(--primary-color); }
          .controls { display:flex; align-items:center; flex-wrap:wrap; justify-content:center; gap:18px; padding:0 12px 14px; }
          .ptz { display:grid; grid-template-columns:repeat(3,var(--control-size)); gap:5px; }
          .ptz button { padding:0; font-size:22px; user-select:none; -webkit-user-select:none; touch-action:none; }
          .ptz button[data-move="up"] { grid-column:2; }
          .ptz button[data-move="left"] { grid-column:1; }
          .zoom { display:flex; flex-direction:column; gap:6px; }
          .intercom { display:flex; gap:6px; flex-wrap:wrap; }
          .intercom button:first-child { color:var(--success-color,#249450); }
          .error { color:var(--error-color,#db4437); padding:0 14px 12px; overflow-wrap:anywhere; font-size:13px; }
          .viewport:fullscreen { width:100vw; height:100vh; aspect-ratio:auto; }
          @media(max-width:360px) { .brand { display:none; } .toolbar { gap:5px; } button { padding:6px 8px; } .volume { width:55px; } }
        </style>
        <ha-card>
          <header><h2></h2><span class="brand">HIKVISION</span></header>
          <div class="viewport"><div class="placeholder" role="status"></div></div>
          <div class="toolbar"></div><div class="controls"></div><div class="error" role="alert" hidden></div>
        </ha-card>`;
      this._viewport = this.shadowRoot.querySelector(".viewport");
      const toolbar = this.shadowRoot.querySelector(".toolbar");
      this._play = this._button("", () => this._playVideo());
      const playIcon = document.createElement("ha-icon"); playIcon.icon = "mdi:play"; this._play.append(playIcon);
      this._sound = this._button("", () => this._setMuted(!this._muted));
      this._soundIcon = document.createElement("ha-icon"); this._sound.append(this._soundIcon);
      this._volumeInput = document.createElement("input");
      this._volumeInput.type = "range";
      this._volumeInput.className = "volume";
      this._volumeInput.min = 0; this._volumeInput.max = 1; this._volumeInput.step = 0.05;
      this._volumeInput.value = this._volume;
      this._volumeInput.addEventListener("input", () => {
        this._volume = Number(this._volumeInput.value);
        if (this._muted && this._volume > 0) this._setMuted(false);
        for (const video of this._videos()) video.volume = this._volume;
      });
      this._streams = document.createElement("select");
      this._streams.addEventListener("change", () => this._switchStream(this._streams.value));
      this._fullscreen = this._button("", () => this._enterFullscreen());
      const fullscreenIcon = document.createElement("ha-icon"); fullscreenIcon.icon = "mdi:fullscreen"; this._fullscreen.append(fullscreenIcon);
      toolbar.append(this._play, this._sound, this._volumeInput, this._streams, this._fullscreen);
      const controls = this.shadowRoot.querySelector(".controls");
      this._ptz = document.createElement("div"); this._ptz.className = "ptz";
      this._zoom = document.createElement("div"); this._zoom.className = "zoom";
      const directions = [
        ["up", "↑", { tilt: 1 }], ["left", "←", { pan: -1 }],
        ["stop", "■", null], ["right", "→", { pan: 1 }], ["down", "↓", { tilt: -1 }],
      ];
      this._moveButtons = [];
      for (const [key, label, vector] of [...directions, ["zoom_in", "+", { zoom: 1 }], ["zoom_out", "−", { zoom: -1 }]]) {
        const button = this._button(label, () => { if (!vector) this._stopMotion(true); });
        button.dataset.move = key;
        if (key === "down") button.style.gridColumn = "2";
        if (vector) {
          button.addEventListener("pointerdown", (event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            button.setPointerCapture?.(event.pointerId);
            this._startMotion(vector);
          });
          for (const event of ["pointerup", "pointercancel", "lostpointercapture", "blur"]) button.addEventListener(event, () => this._stopMotion());
          button.addEventListener("keydown", (event) => {
            if (![" ", "Enter"].includes(event.key) || event.repeat) return;
            event.preventDefault(); this._startMotion(vector);
          });
          button.addEventListener("keyup", (event) => {
            if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); this._stopMotion(); }
          });
        }
        this._moveButtons.push(button);
        (key.startsWith("zoom") ? this._zoom : this._ptz).append(button);
      }
      this._intercom = document.createElement("div"); this._intercom.className = "intercom";
      for (const action of ["answer", "reject", "hangup"]) {
        const button = this._button("", () => this._intercomAction(action));
        button.dataset.action = action; this._intercom.append(button);
      }
      controls.append(this._ptz, this._zoom, this._intercom);
    }

    _streamEntities() {
      const states = this._hass.states;
      const base = metadata(states[this._config.entity]);
      const ids = new Set([this._config.entity]);
      if (this._config.sub_stream) ids.add(this._config.sub_stream);
      if (base) for (const state of Object.values(states)) {
        const data = metadata(state);
        if (cameraId(state.entity_id) && data?.config_entry_id === base.config_entry_id && data.channel_id === base.channel_id && data.stream_type >= 1 && data.stream_type <= 11) ids.add(state.entity_id);
      }
      return [...ids].filter((id) => states[id]).sort((a, b) => (metadata(states[a])?.stream_type || 0) - (metadata(states[b])?.stream_type || 0));
    }

    _update() {
      if (!this._config || !this._hass || !this._viewport) return;
      const state = this._hass.states[this._entity];
      const data = metadata(state);
      const available = !!state && !["unavailable", "unknown"].includes(state.state);
      this.shadowRoot.querySelector("h2").textContent = this._config.name || state?.attributes.friendly_name || this._entity;
      for (const [button, key] of [[this._play, "play"], [this._sound, this._muted ? "sound" : "mute"], [this._fullscreen, "fullscreen"]]) {
        button.title = text(this._hass, key); button.setAttribute("aria-label", button.title);
      }
      this._soundIcon.icon = this._muted ? "mdi:volume-off" : "mdi:volume-high";
      this._sound.setAttribute("aria-pressed", String(!this._muted));
      this._volumeInput.setAttribute("aria-label", text(this._hass, "volume"));
      this._streams.setAttribute("aria-label", text(this._hass, "stream"));
      const ids = this._streamEntities();
      const optionsKey = JSON.stringify([ids, this._hass.language]);
      if (optionsKey !== this._optionsKey) {
        this._optionsKey = optionsKey;
        this._streams.replaceChildren(...ids.map((id) => {
          const option = document.createElement("option"); option.value = id;
          const kind = metadata(this._hass.states[id])?.stream_type;
          option.textContent = kind === 1 ? text(this._hass, "main") : kind === 2 ? text(this._hass, "sub") : this._hass.states[id].attributes.friendly_name || id;
          return option;
        }));
      }
      this._streams.value = this._entity; this._streams.hidden = ids.length < 2;
      this._ptz.hidden = this._zoom.hidden = !(this._config.show_ptz && data?.ptz);
      for (const button of this._moveButtons) {
        button.title = text(this._hass, button.dataset.move); button.setAttribute("aria-label", button.title); button.disabled = !available;
      }
      this._intercom.hidden = !(this._config.show_intercom && data?.intercom);
      for (const button of this._intercom.children) {
        button.textContent = text(this._hass, button.dataset.action); button.disabled = !available || this._intercomBusy;
      }
      this._sound.disabled = this._play.disabled = !available;
      if (this._motion && !data?.ptz) this._stopMotion();
      if (!available) {
        this._stopMotion(); this._removePlayer();
        this._placeholder(text(this._hass, "missing"));
      } else if (this._player) {
        this._player.hass = this._hass; this._player.stateObj = state;
      } else if (this.isConnected) this._mountPlayer();
    }

    _placeholder(message) {
      const placeholder = this.shadowRoot.querySelector(".placeholder");
      placeholder.hidden = false; placeholder.textContent = message;
    }

    _removePlayer() {
      ++this._generation;
      this._mounting = false;
      this._videoObserver?.disconnect(); this._videoObserver = undefined;
      this._player?.remove(); this._player = undefined;
    }

    async _mountPlayer() {
      if (this._mounting) return;
      this._mounting = true;
      const generation = ++this._generation;
      this._placeholder(text(this._hass, "loading"));
      try {
        await loadPlayer(this._entity);
        if (generation !== this._generation || !this.isConnected) return;
        const player = document.createElement("ha-camera-stream");
        player.hass = this._hass; player.stateObj = this._hass.states[this._entity];
        player.muted = this._muted; player.controls = false;
        this._playerStartedMuted = this._muted;
        player.aspectRatio = 16 / 9; player.fitMode = "contain";
        this._player = player;
        this._viewport.append(player);
        this.shadowRoot.querySelector(".placeholder").hidden = true;
        this._videoObserver = new MutationObserver(() => this._observeVideos());
        this._observedRoots = new WeakSet(); this._observedVideos = new WeakSet();
        this._observeVideos();
      } catch (error) {
        if (generation === this._generation) this._placeholder(text(this._hass, "player_error"));
      } finally { if (generation === this._generation) this._mounting = false; }
    }

    // HA players use nested open shadow roots; observe new roots without polling.
    _observeVideos() {
      const visit = (root) => {
        if (!root) return;
        if (!this._observedRoots.has(root)) {
          this._observedRoots.add(root); this._videoObserver.observe(root, { childList: true, subtree: true });
        }
        for (const node of root.querySelectorAll("*")) {
          if (node.localName === "video" && !this._observedVideos.has(node)) {
            this._observedVideos.add(node); node.volume = this._volume;
            node.addEventListener("loadedmetadata", () => { node.volume = this._volume; });
          }
          if (node.shadowRoot) visit(node.shadowRoot);
          else if (node.localName?.includes("-") && !customElements.get(node.localName)) {
            customElements.whenDefined(node.localName).then(() => { if (this._player) this._observeVideos(); });
          }
        }
      };
      visit(this._player.shadowRoot || this._player);
    }

    _videos() {
      const videos = [];
      const visit = (root) => {
        if (!root) return;
        videos.push(...root.querySelectorAll("video"));
        for (const node of root.querySelectorAll("*")) if (node.shadowRoot) visit(node.shadowRoot);
      };
      visit(this._player?.shadowRoot); return videos;
    }

    _setMuted(value) {
      this._muted = value;
      // HA 2026.2 omits the remote audio track when it starts muted. Recreate
      // the native player on unmute so the audio track is actually attached.
      if (!value && this._playerStartedMuted) this._removePlayer();
      else if (this._player) this._player.muted = value;
      this._update();
    }

    async _playVideo() {
      this._showError("");
      const videos = this._videos();
      if (videos.some((video) => video.paused)) {
        try { await Promise.all(videos.map((video) => video.play())); } catch (error) { this._showError(error); }
      } else { this._removePlayer(); this._update(); }
    }

    _switchStream(entity) {
      this._stopMotion(); this._showError("");
      this._entity = entity; this._removePlayer(); this._update();
    }

    async _enterFullscreen() {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else if (this._viewport.requestFullscreen) await this._viewport.requestFullscreen();
        else {
          const video = this._videos()[0];
          if (!video?.webkitEnterFullscreen) throw new Error("Fullscreen is unavailable");
          video.webkitEnterFullscreen();
        }
      } catch (error) { this._showError(error); }
    }

    _showError(error) {
      const element = this.shadowRoot.querySelector(".error");
      if (!element) return;
      element.textContent = error ? String(error.message || error) : ""; element.hidden = !error;
    }

    _enqueue(callback) {
      this._queue = this._queue.then(callback).catch((error) => this._showError(error));
      return this._queue;
    }

    _startMotion(vector) {
      const data = metadata(this._hass?.states[this._entity]);
      if (this._motion || !data?.ptz || !this.isConnected) return;
      this._showError("");
      const context = { hass: this._hass, data: { config_entry_id: data.config_entry_id, channel_id: data.channel_id } };
      const speed = this._config.ptz_speed;
      this._motion = context;
      // Queue stop after move even when a quick release precedes its response.
      this._enqueue(() => context.hass.callService("hikvision_next", "ptz_move", {
        ...context.data, ...Object.fromEntries(Object.entries(vector).map(([axis, direction]) => [axis, direction * speed])),
      }));
      this._motionTimer = setTimeout(() => this._stopMotion(), 10000);
    }

    _stopMotion(force = false) {
      let context = this._motion;
      this._motion = undefined; clearTimeout(this._motionTimer);
      if (!context && force) {
        const data = metadata(this._hass?.states[this._entity]);
        if (data?.ptz) context = { hass: this._hass, data: { config_entry_id: data.config_entry_id, channel_id: data.channel_id } };
      }
      if (context) this._enqueue(() => context.hass.callService("hikvision_next", "ptz_stop", context.data));
    }

    async _intercomAction(action) {
      const data = metadata(this._hass?.states[this._entity]);
      if (!data?.intercom || this._intercomBusy) return;
      this._intercomBusy = true; this._showError(""); this._update();
      try { await this._hass.callService("hikvision_next", `intercom_${action}`, { config_entry_id: data.config_entry_id }); }
      catch (error) { this._showError(error); }
      finally { this._intercomBusy = false; this._update(); }
    }
  }

  class HikvisionCameraCardEditor extends HTMLElement {
    constructor() { super(); this.attachShadow({ mode: "open" }); }
    setConfig(config) { this._config = { ...config }; this._render(); }
    set hass(hass) { this._hass = hass; this._render(); }
    _render() {
      if (!this._config || !this._hass) return;
      const key = JSON.stringify([this._config, this._hass.language, Object.keys(this._hass.states).filter(cameraId)]);
      if (key === this._renderKey) return;
      this._renderKey = key;
      this.shadowRoot.innerHTML = `<style>:host{display:block}label{display:flex;flex-direction:column;gap:6px;margin:14px 0}select,input{font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:10px;min-height:24px}label.check{flex-direction:row;align-items:center}input[type=checkbox]{width:22px;height:22px}</style>`;
      const ids = Object.keys(this._hass.states).filter(cameraId);
      for (const field of ["entity", "sub_stream", "name", "ptz_speed", "muted", "show_ptz", "show_intercom"]) {
        const label = document.createElement("label");
        const caption = document.createElement("span"); caption.textContent = text(this._hass, field === "entity" ? "camera" : field);
        let input;
        if (["entity", "sub_stream"].includes(field)) {
          input = document.createElement("select");
          const empty = document.createElement("option"); empty.value = ""; empty.textContent = "—"; input.append(empty);
          for (const id of new Set([...ids, this._config[field]].filter(Boolean))) {
            const option = document.createElement("option"); option.value = id;
            option.textContent = `${this._hass.states[id]?.attributes.friendly_name || id} (${id})`; input.append(option);
          }
          input.value = this._config[field] || "";
        } else {
          input = document.createElement("input");
          input.type = ["muted", "show_ptz", "show_intercom"].includes(field) ? "checkbox" : field === "ptz_speed" ? "number" : "text";
          if (input.type === "checkbox") { label.className = "check"; input.checked = this._config[field] !== false; }
          else { input.value = this._config[field] ?? (field === "ptz_speed" ? 40 : ""); }
          if (field === "ptz_speed") { input.min = 1; input.max = 100; input.step = 1; }
        }
        input.dataset.field = field;
        input.addEventListener("change", () => {
          if (field === "ptz_speed" && !input.checkValidity()) { input.reportValidity(); return; }
          const value = input.type === "checkbox" ? input.checked : field === "ptz_speed" ? Number(input.value) : input.value;
          this._config = { ...this._config, [field]: value };
          if (value === "") delete this._config[field];
          fire(this, "config-changed", { config: this._config });
        });
        label.append(caption, input); this.shadowRoot.append(label);
      }
    }
  }

  class HikvisionCameraControl extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `
        <style>
          :host { display:block; max-width:1000px; padding:16px; margin:0 auto; box-sizing:border-box; }
          .heading { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:0 0 16px; }
          h1 { font-size:20px; font-weight:500; margin:0; flex:1; }
          label { display:flex; align-items:center; gap:10px; }
          select { font:inherit; padding:10px; max-width:100%; color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:8px; }
          select:focus-visible, a:focus-visible { outline:2px solid var(--primary-color); outline-offset:2px; }
          a { color:var(--primary-color); text-decoration:none; }
          .message { padding:24px 16px; color:var(--secondary-text-color); background:var(--card-background-color); border-radius:12px; }
          [hidden] { display:none !important; }
          @media(max-width:500px) { :host { padding:8px; } .heading { padding:8px; } label { flex-basis:100%; } select { flex:1; min-width:0; } }
        </style>
        <div class="heading"><h1></h1><a hidden></a><label><span></span><select></select></label></div>
        <div class="message" role="status"></div><div class="content"></div>`;
      this._select = this.shadowRoot.querySelector("select");
      this._select.addEventListener("change", () => {
        this._selection = this._select.value;
        this._update();
      });
      this._routeChanged = () => this._update();
    }

    setConfig() { this._update(); }
    set hass(hass) { this._hass = hass; this._update(); }
    getCardSize() { return 10; }
    getGridOptions() { return { columns: "full" }; }

    connectedCallback() {
      window.addEventListener("popstate", this._routeChanged);
      window.addEventListener("location-changed", this._routeChanged);
      this._update();
    }

    disconnectedCallback() {
      window.removeEventListener("popstate", this._routeChanged);
      window.removeEventListener("location-changed", this._routeChanged);
      this._dropCard();
    }

    _dropCard() {
      this._card?.remove();
      this._card = undefined;
      this._cardEntity = undefined;
    }

    _update() {
      if (!this._hass || !this.isConnected) return;
      const query = new URLSearchParams(window.location.search);
      const entry = query.get("entry");
      const requestedChannel = query.get("channel");
      const scope = JSON.stringify([entry, requestedChannel]);
      if (scope !== this._scope) {
        this._scope = scope;
        this._selection = undefined;
      }
      // One option per channel. Prefer the main stream, or the first enabled
      // alternative if its main entity is disabled. Never start a video grid.
      const groups = new Map();
      for (const state of Object.values(this._hass.states)) {
        const data = metadata(state);
        if (!cameraId(state.entity_id) || !data || (entry && data.config_entry_id !== entry) || data.stream_type < 1 || data.stream_type > 11) continue;
        const key = `${data.config_entry_id}:${data.channel_id}`;
        const previous = groups.get(key);
        if (!previous || data.stream_type < metadata(previous).stream_type) groups.set(key, state);
      }
      const options = [...groups].sort((a, b) =>
        metadata(a[1]).channel_id - metadata(b[1]).channel_id || a[0].localeCompare(b[0]));
      if (this._selection === undefined) {
        this._selection = (requestedChannel !== null
          ? options.find(([, state]) => String(metadata(state).channel_id) === requestedChannel)
          : options[0])?.[0];
      }
      const optionsKey = JSON.stringify([this._hass.language, options.map(([key, state]) => [key, state.attributes.friendly_name, state.entity_id])]);
      if (optionsKey !== this._optionsKey) {
        this._optionsKey = optionsKey;
        this._select.replaceChildren(...options.map(([key, state]) => {
          const option = document.createElement("option"); option.value = key;
          option.textContent = `${state.attributes.friendly_name || state.entity_id} · ${metadata(state).channel_id}`;
          return option;
        }));
      }
      this._select.value = this._selection || "";
      this.shadowRoot.querySelector("label").hidden = options.length < 2;
      this.shadowRoot.querySelector("label span").textContent = text(this._hass, "choose_camera");
      this.shadowRoot.querySelector("h1").textContent = text(this._hass, "control_page");
      const message = this.shadowRoot.querySelector(".message");
      const state = groups.get(this._selection);
      const link = this.shadowRoot.querySelector("a");
      const deviceId = state && this._hass.entities?.[state.entity_id]?.device_id;
      link.hidden = !deviceId;
      if (deviceId) link.href = `/config/devices/device/${encodeURIComponent(deviceId)}`;
      else link.removeAttribute("href");
      link.textContent = text(this._hass, "device_details");
      message.hidden = !!state;
      if (!state) {
        message.textContent = text(this._hass, "no_camera");
        this._dropCard();
        return;
      }
      if (!this._card || this._cardEntity !== state.entity_id) {
        this._dropCard();
        this._card = document.createElement(TYPE);
        this._card.setConfig({ entity: state.entity_id });
        this._card.hass = this._hass;
        this._cardEntity = state.entity_id;
        this.shadowRoot.querySelector(".content").append(this._card);
      } else this._card.hass = this._hass;
    }
  }

  if (!customElements.get(TYPE)) customElements.define(TYPE, HikvisionCameraCard);
  if (!customElements.get("hikvision-camera-control")) customElements.define("hikvision-camera-control", HikvisionCameraControl);
  if (!customElements.get(`${TYPE}-editor`)) customElements.define(`${TYPE}-editor`, HikvisionCameraCardEditor);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((card) => card.type === TYPE)) window.customCards.push({
    type: TYPE, name: "Hikvision Camera", preview: true, description: TEXT.en.description,
  });
})();
