/**
 * @class ViewerSidebar - unified controls for marker gating and image channels.
 */
class ViewerSidebar {
    constructor(config, columns, dataLayer, eventHandler, channelList, gatingList) {
        this.config = config;
        this.columns = [...columns];
        this.dataLayer = dataLayer;
        this.eventHandler = eventHandler;
        this.channelList = channelList;
        this.gatingList = gatingList;
        this.databaseDescription = {};
        this.gateMarker = null;
        this.gateSlider = null;
        this.channelSlots = [];
        this.channelSlotSliders = new Map();
        // Remembers a manually-set intensity range per marker name (not per slot), so
        // switching a slot's marker away and back doesn't discard what the user tuned.
        this.markerRangeOverrides = new Map();
        this.colorPickers = new Map();
        this.markerSelects = new Map();
        this.gateMarkerSelect = null;
        this.gateMarkerChangeTimer = null;
        this._saveChannelsTimer = null;
        this._saveGatingTimer = null;
        this._restoring = false;
        this.maxChannelSlots = 15;
        this.initialChannelSlots = 4;
        this.defaultColors = [
            { label: "Blue", hex: "#2388ff", rgb: { r: 35, g: 136, b: 255 } },
            { label: "Red", hex: "#ff2d2d", rgb: { r: 255, g: 45, b: 45 } },
            { label: "Green", hex: "#2bd46f", rgb: { r: 43, g: 212, b: 111 } },
            { label: "White", hex: "#ffffff", rgb: { r: 255, g: 255, b: 255 } },
        ];
    }

    async init(databaseDescription) {
        this.databaseDescription = databaseDescription;
        this.setupSidebarShell();
        this.populateGateSelect();
        this.bindActions();

        const maxLabel = document.getElementById("max-channels");
        if (maxLabel) maxLabel.textContent = this.maxChannelSlots;

        const [savedChannels, savedGating] = await Promise.all([
            this.dataLayer.getSavedChannelList(),
            this.dataLayer.getSavedGatingList(),
        ]);

        // Suppressed while restoring: applySavedChannels/applySavedGating reuse the same
        // setters live edits use, which otherwise schedule an autosave on every call -
        // turning "load from DB" into "load from DB, then immediately write back to DB".
        this._restoring = true;
        if (savedChannels && savedChannels.length) {
            this.applySavedChannels(savedChannels);
        } else {
            this.initChannelSlots();
            this.applyInitialChannels();
        }

        if (savedGating && savedGating.length) {
            this.applySavedGating(savedGating);
        } else {
            this.setGateMarker(this.columns[1] || this.columns[0], { enableSlot: false });
        }
        this._restoring = false;

        if (!(savedChannels && savedChannels.length)) this.persistChannelList();
        if (!(savedGating && savedGating.length)) this.persistGatingList();
    }

    setupSidebarShell() {
        const collapseButton = document.getElementById("sidebar_collapse_button");
        const expandButton = document.getElementById("sidebar_expand_button");
        const shell = document.getElementById("bodyDiv");
        const toggleSidebar = () => {
            if (shell) {
                shell.classList.toggle("sidebar-collapsed");
            }
        };
        if (collapseButton) {
            collapseButton.addEventListener("click", toggleSidebar);
        }
        if (expandButton) {
            expandButton.addEventListener("click", toggleSidebar);
        }
    }

    bindActions() {
        const gateAuto = document.getElementById("gate_auto_button");
        gateAuto.addEventListener("click", async () => {
            gateAuto.disabled = true;
            gateAuto.classList.add("auto-loading");
            try {
                await this.autoGate();
            } finally {
                gateAuto.disabled = false;
                gateAuto.classList.remove("auto-loading");
            }
        });

        const addButton = document.getElementById("add_channel_button");
        addButton.addEventListener("click", () => this.addFirstAvailableChannel());

        window.addEventListener("resize", () => {
            this.redrawGateSlider();
            this.redrawChannelSliders();
        });
    }

    populateGateSelect() {
        const names = this.getGateMarkerNames();
        const mount = document.getElementById("gate_marker_select");
        if (!this.gateMarkerSelect) {
            this.gateMarkerSelect = new SearchableSelect(mount, {
                options: names,
                value: this.gateMarker || "",
                placeholder: "Search markers…",
                onChange: (name) => {
                    window.clearTimeout(this.gateMarkerChangeTimer);
                    this.gateMarkerChangeTimer = window.setTimeout(() => {
                        this.setGateMarker(name);
                    }, 0);
                },
            });
        } else {
            this.gateMarkerSelect.setOptions(names);
        }
    }

    getGateMarkerNames() {
        const names = [...this.columns];
        if (!names.includes("Area") && this.databaseDescription.Area) {
            names.push("Area");
        }
        return names.filter((name) => this.databaseDescription[this.dataLayer.getFullChannelName(name)]);
    }

    initChannelSlots() {
        const slotList = document.getElementById("channel_slot_list");
        slotList.innerHTML = "";
        this.channelSlots = [...Array(this.initialChannelSlots).keys()].map((slotIndex) => {
            const color = this.getDefaultColor(slotIndex);
            const name = this.columns[slotIndex] || "";
            const slot = {
                index: slotIndex,
                name,
                color: color.rgb,
                colorHex: color.hex,
                enabled: slotIndex === 0 && Boolean(name),
                visible: Boolean(name),
                expanded: false,
                sliderDirty: false,
                range: this.getImageRange(name),
                userColorChanged: false,
                userRangeChanged: false,
                autoLeveled: false,
                autoLeveling: false,
            };
            slotList.appendChild(this.createChannelSlot(slot));
            return slot;
        });
        this.updateSelectedCount();
        this.redrawChannelSliders();
    }

    createChannelSlot(slot) {
        const row = document.createElement("div");
        row.classList.add("channel-slot");
        row.classList.toggle("is-hidden", !slot.visible);
        row.classList.toggle("is-disabled", !slot.enabled);
        row.setAttribute("data-slot", slot.index);
        row.style.setProperty("--slot-color", slot.colorHex);

        const top = document.createElement("div");
        top.classList.add("channel-slot-top");
        row.appendChild(top);

        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.classList.add("channel-toggle-switch");
        toggle.checked = slot.enabled;
        toggle.title = "Toggle channel";
        toggle.addEventListener("change", (event) => {
            this.setSlotEnabled(slot.index, event.target.checked);
        });
        top.appendChild(toggle);

        const colorMount = document.createElement("div");
        top.appendChild(colorMount);
        const colorPicker = new ColorSwatchPicker(colorMount, {
            value: slot.colorHex,
            onChange: (hex) => this.setSlotColor(slot.index, hex, true),
        });
        this.colorPickers.set(slot.index, colorPicker);

        const comboMount = document.createElement("div");
        top.appendChild(comboMount);
        const markerSelect = new SearchableSelect(comboMount, {
            options: this.columns,
            value: slot.name,
            placeholder: "Select marker…",
            describeOption: (name) => this.describeMarkerOption(name, slot.index),
            onChange: (name) => this.setSlotMarker(slot.index, name, { keepColor: true, enable: true }),
        });
        this.markerSelects.set(slot.index, markerSelect);

        const expandToggle = document.createElement("button");
        expandToggle.type = "button";
        expandToggle.classList.add("channel-slot-expand-toggle");
        expandToggle.classList.toggle("is-expanded", Boolean(slot.expanded));
        expandToggle.title = "Show threshold range";
        expandToggle.innerHTML = '<span class="fas fa-chevron-down"></span>';
        expandToggle.addEventListener("click", () => this.toggleSlotExpanded(slot.index));
        top.appendChild(expandToggle);

        const remove = document.createElement("button");
        remove.type = "button";
        remove.classList.add("slot-remove-button");
        remove.title = "Remove channel slot";
        remove.innerHTML = '<span class="fas fa-times"></span>';
        remove.addEventListener("click", () => this.removeChannelSlot(slot.index));
        top.appendChild(remove);

        const detail = document.createElement("div");
        detail.classList.add("channel-slot-detail");
        detail.classList.toggle("is-expanded", Boolean(slot.expanded));

        const detailHeader = document.createElement("div");
        detailHeader.classList.add("slot-detail-header");

        const values = document.createElement("div");
        values.classList.add("range-readout", "slot-range-readout");
        values.innerHTML = `<span id="channel_slot_min_${slot.index}">0.00</span><span id="channel_slot_max_${slot.index}">0.00</span>`;
        detailHeader.appendChild(values);

        const auto = document.createElement("button");
        auto.type = "button";
        auto.classList.add("slot-auto-button");
        auto.title = "Auto-set threshold range from data";
        auto.textContent = "Auto";
        auto.addEventListener("click", () => this.autoChannel(slot.index, { force: true }));
        detailHeader.appendChild(auto);

        detail.appendChild(detailHeader);

        const slider = document.createElement("div");
        slider.classList.add("sidebar-slider");
        slider.setAttribute("id", `channel_slot_slider_${slot.index}`);
        detail.appendChild(slider);

        row.appendChild(detail);

        return row;
    }

    applyInitialChannels() {
        this.channelSlots.forEach((slot) => {
            if (slot.name && slot.enabled) {
                this.activateChannel(slot);
            }
        });
        this.updateSelectedCount();
    }

    setGateMarker(name, options = {}) {
        if (!name) return;
        if (name === this.gateMarker && !options.force) return;
        const enableSlot = options.enableSlot !== false;
        this.gateMarker = name;
        if (this.gateMarkerSelect) {
            this.gateMarkerSelect.setValue(name);
        }
        this.ensureGateSelection(name);
        this.redrawGateSlider();
        this.drawGateDistribution();
        if (options.syncSlot !== false) {
            this.setSlotMarker(1, name, { keepColor: true, enable: enableSlot, reveal: enableSlot });
        }
        this.scheduleSaveGating();
    }

    ensureGateSelection(name) {
        const fullName = this.dataLayer.getFullChannelName(name);
        const range = this.gatingList.gating_channels[fullName] || this.getGateRange(name);
        this.gatingList.selections = {};
        this.gatingList.gating_channels[fullName] = range;
        this.gatingList.selections[fullName] = range;
        this.updateGateReadout(range);
        this.eventHandler.trigger(CSVGatingList.events.GATING_BRUSH_MOVE, this.gatingList.selections);
        this.eventHandler.trigger(CSVGatingList.events.GATING_BRUSH_END, this.gatingList.selections);
    }

    redrawGateSlider() {
        if (!this.gateMarker) return;
        const target = document.getElementById("gate_slider");
        target.innerHTML = "";
        const range = this.getGateRange(this.gateMarker);
        const values = this.gatingList.gating_channels[this.dataLayer.getFullChannelName(this.gateMarker)] || range;
        const width = Math.max(180, target.getBoundingClientRect().width - 16);
        const slider = d3.sliderBottom()
            .min(range[0])
            .max(range[1])
            .width(width)
            .ticks(0)
            .tickValues([])
            .default(values)
            .fill("#f36f45")
            .handle(d3.symbol().type(d3.symbolCircle).size(120))
            .on("onchange", (value) => this.setGateRange(value, CSVGatingList.events.GATING_BRUSH_MOVE))
            .on("end", (value) => this.setGateRange(value, CSVGatingList.events.GATING_BRUSH_END));

        this.gateSlider = slider;
        d3.select(target)
            .append("svg")
            .attr("width", width + 16)
            .attr("height", 44)
            .append("g")
            .attr("transform", "translate(8,18)")
            .call(slider);
        this.updateGateReadout(values);
    }

    setGateRange(values, eventName) {
        const fullName = this.dataLayer.getFullChannelName(this.gateMarker);
        const normalized = this.normalizeRange(values, this.dataLayer.isTransformed());
        this.gatingList.gating_channels[fullName] = normalized;
        this.gatingList.selections = {};
        this.gatingList.selections[fullName] = normalized;
        this.updateGateReadout(normalized);
        this.updateGateThresholdLines(normalized);
        this.eventHandler.trigger(eventName, this.gatingList.selections);
        if (eventName === CSVGatingList.events.GATING_BRUSH_END) {
            this.scheduleSaveGating();
        }
    }

    async autoGate() {
        if (!this.gateMarker) return;
        if (!(this.gateMarker in this.gatingList.hasGatingGMM)) {
            await this.gatingList.getGatingGMM(this.gateMarker);
        }
        const packet = this.gatingList.hasGatingGMM[this.gateMarker];
        if (!packet || packet.gate === undefined) return;
        const range = this.getGateRange(this.gateMarker);
        const transformed = this.dataLayer.isTransformed();
        const gate = transformed ? parseFloat(packet.gate) : Math.floor(parseFloat(packet.gate));
        const values = [gate, range[1]];
        if (this.gateSlider) {
            this.gateSlider.silentValue(values);
        }
        this.setGateRange(values, CSVGatingList.events.GATING_BRUSH_END);
        this.redrawGateSlider();
    }

    drawGateDistribution() {
        const target = document.getElementById("gate_distribution_plot");
        target.innerHTML = "";
        this.gateDistributionScale = null;
        if (!this.gateMarker) return;
        const fullName = this.dataLayer.getFullChannelName(this.gateMarker);
        const desc = this.databaseDescription[fullName];
        const histogram = desc?.histogram || [];
        if (!histogram.length) return;

        const box = target.getBoundingClientRect();
        const width = Math.max(220, box.width || 280);
        const height = 120;
        const margin = { top: 12, right: 10, bottom: 24, left: 28 };
        const innerWidth = width - margin.left - margin.right;
        const innerHeight = height - margin.top - margin.bottom;
        const xDomain = d3.extent(histogram, (d) => d.x);
        const yMax = d3.max(histogram, (d) => d.y);
        const xScale = d3.scaleLinear().domain(xDomain).range([0, innerWidth]);
        const yScale = d3.scaleLinear().domain([0, yMax]).range([innerHeight, 0]);
        const line = d3.line()
            .x((d) => xScale(d.x))
            .y((d) => yScale(d.y))
            .curve(d3.curveMonotoneX);
        const values = this.gatingList.gating_channels[fullName] || this.getGateRange(this.gateMarker);

        const svg = d3.select(target)
            .append("svg")
            .attr("width", width)
            .attr("height", height);
        const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
        g.append("path")
            .datum(histogram)
            .attr("class", "sidebar-distribution-line")
            .attr("d", line);
        g.append("g")
            .attr("class", "gate-threshold-lines")
            .selectAll("line")
            .data(values)
            .enter()
            .append("line")
            .attr("class", "gate-threshold-line")
            .attr("x1", (value) => xScale(value))
            .attr("x2", (value) => xScale(value))
            .attr("y1", 0)
            .attr("y2", innerHeight);
        g.append("g")
            .attr("class", "distribution-axis")
            .attr("transform", `translate(0,${innerHeight})`)
            .call(d3.axisBottom(xScale).ticks(3).tickFormat(d3.format(".2f")));

        this.gateDistributionScale = xScale;
    }

    // Cheap per-tick update during a drag: reposition the existing threshold lines instead of
    // tearing down and rebuilding the whole histogram/axis (which was the source of drag lag).
    updateGateThresholdLines(values) {
        if (!this.gateDistributionScale) {
            this.drawGateDistribution();
            return;
        }
        const xScale = this.gateDistributionScale;
        d3.select("#gate_distribution_plot")
            .selectAll(".gate-threshold-line")
            .data(values)
            .attr("x1", (value) => xScale(value))
            .attr("x2", (value) => xScale(value));
    }

    setSlotMarker(slotIndex, name, options = {}) {
        const slot = this.channelSlots[slotIndex];
        if (!slot || !name) return;
        const markerChanged = slot.name !== name;
        const enablesSlot = options.enable && !slot.enabled;
        const revealsSlot = options.reveal && !slot.visible;
        if (!markerChanged && !enablesSlot && !revealsSlot && !options.force) return;
        if (slot.name && slot.enabled && markerChanged) {
            this.deactivateChannel(slot);
        }
        this.disableDuplicateChannels(name, slotIndex);
        slot.name = name;
        if (markerChanged) {
            const override = this.markerRangeOverrides.get(name);
            if (override) {
                slot.range = [...override];
                slot.userRangeChanged = true;
                slot.autoLeveled = true;
            } else {
                slot.range = this.getImageRange(name);
                slot.userRangeChanged = false;
                slot.autoLeveled = false;
            }
            slot.autoLeveling = false;
            slot.expanded = true;
            slot.sliderDirty = true;
        }
        if (!options.keepColor) {
            this.setSlotColor(slotIndex, this.getDefaultColor(slotIndex).hex, false);
        }
        if (options.enable) {
            slot.enabled = true;
        }
        if (options.reveal || options.enable) {
            slot.visible = true;
        }
        this.syncSlotDom(slot);
        this.applySlotExpansion(slot);
        if (slot.enabled) {
            this.activateChannel(slot);
            this.autoLevelChannelIfNeeded(slot);
        }
        this.updateSelectedCount();
        this.scheduleSaveChannels();
    }

    setSlotEnabled(slotIndex, enabled) {
        const slot = this.channelSlots[slotIndex];
        if (!slot || !slot.name) return;
        const wasEnabled = slot.enabled;
        slot.enabled = enabled;
        slot.visible = true;
        if (enabled) {
            this.disableDuplicateChannels(slot.name, slotIndex);
            this.activateChannel(slot);
            if (!wasEnabled) {
                this.autoLevelChannelIfNeeded(slot);
            }
        } else {
            this.deactivateChannel(slot);
        }
        this.syncSlotDom(slot);
        this.updateSelectedCount();
        this.scheduleSaveChannels();
    }

    setSlotColor(slotIndex, hex, userColorChanged) {
        const slot = this.channelSlots[slotIndex];
        if (!slot) return;
        slot.colorHex = hex;
        slot.color = this.hexToRgb(hex);
        slot.userColorChanged = Boolean(userColorChanged || slot.userColorChanged);
        this.syncSlotDom(slot);
        if (slot.enabled && slot.name) {
            this.eventHandler.trigger(ChannelList.events.COLOR_TRANSFER_CHANGE, {
                name: slot.name,
                type: "white",
                color: d3.rgb(slot.color.r, slot.color.g, slot.color.b),
            });
        }
        this.scheduleSaveChannels();
    }

    activateChannel(slot) {
        const fullName = this.dataLayer.getFullChannelName(slot.name);
        const channelIdx = imageChannels[fullName];
        if (channelIdx === undefined) return;
        this.channelList.image_channels[slot.name] = slot.range;
        this.channelList.rangeConnector[channelIdx] = this.toImageConnectorRange(slot.range);
        this.channelList.colorConnector[channelIdx] = { color: slot.color };
        if (!this.channelList.selections.includes(slot.name)) {
            this.channelList.selections.push(slot.name);
        }
        this.channelList.sel[fullName] = slot.range;
        this.eventHandler.trigger(ChannelList.events.CHANNELS_CHANGE, {
            selections: this.channelList.selections,
            name: slot.name,
            status: true,
        });
        this.eventHandler.trigger(ChannelList.events.COLOR_TRANSFER_CHANGE, {
            name: slot.name,
            type: "white",
            color: d3.rgb(slot.color.r, slot.color.g, slot.color.b),
        });
        this.eventHandler.trigger(ChannelList.events.BRUSH_MOVE, {
            name: slot.name,
            dataRange: [...slot.range],
        });
    }

    deactivateChannel(slot) {
        const fullName = this.dataLayer.getFullChannelName(slot.name);
        this.channelList.selections = _.pull(this.channelList.selections, slot.name);
        delete this.channelList.sel[fullName];
        this.eventHandler.trigger(ChannelList.events.CHANNELS_CHANGE, {
            selections: this.channelList.selections,
            name: slot.name,
            status: false,
        });
    }

    disableDuplicateChannels(name, currentSlotIndex) {
        this.channelSlots.forEach((slot) => {
            if (slot.index !== currentSlotIndex && slot.enabled && slot.name === name) {
                slot.enabled = false;
                this.deactivateChannel(slot);
                this.syncSlotDom(slot);
            }
        });
    }

    redrawChannelSliders() {
        this.channelSlots.filter((slot) => slot.visible && slot.expanded).forEach((slot) => this.redrawChannelSlider(slot));
    }

    redrawChannelSlider(slot) {
        if (!slot || !slot.name) return;
        this.updateSlotReadout(slot);
        if (!slot.expanded) {
            slot.sliderDirty = true;
            return;
        }
        slot.sliderDirty = false;
        const target = document.getElementById(`channel_slot_slider_${slot.index}`);
        if (!target) return;
        target.innerHTML = "";
        const range = this.getImageRange(slot.name);
        const width = Math.max(180, target.getBoundingClientRect().width - 16);
        const slider = d3.sliderBottom(d3.scaleLog())
            .min(Math.max(range[0], 1))
            .max(Math.max(range[1], 2))
            .width(width)
            .ticks(0)
            .tickValues([])
            .default([Math.max(slot.range[0], 1), Math.max(slot.range[1], 2)])
            .fill("#38bdf8")
            .handle(d3.symbol().type(d3.symbolCircle).size(120))
            .on("onchange", (value) => this.setSlotRange(slot.index, value, true))
            .on("end", () => this.scheduleSaveChannels());

        this.channelSlotSliders.set(slot.index, slider);
        d3.select(target)
            .append("svg")
            .attr("width", width + 16)
            .attr("height", 44)
            .append("g")
            .attr("transform", "translate(8,18)")
            .call(slider);
    }

    toggleSlotExpanded(slotIndex) {
        const slot = this.channelSlots[slotIndex];
        if (!slot) return;
        slot.expanded = !slot.expanded;
        this.applySlotExpansion(slot);
    }

    applySlotExpansion(slot) {
        const row = document.querySelector(`.channel-slot[data-slot="${slot.index}"]`);
        if (!row) return;
        const detail = row.querySelector(".channel-slot-detail");
        const toggle = row.querySelector(".channel-slot-expand-toggle");
        if (detail) detail.classList.toggle("is-expanded", Boolean(slot.expanded));
        if (toggle) toggle.classList.toggle("is-expanded", Boolean(slot.expanded));
        if (slot.expanded && (slot.sliderDirty || !this.channelSlotSliders.has(slot.index))) {
            this.redrawChannelSlider(slot);
        }
    }

    describeMarkerOption(name, currentSlotIndex) {
        const activeElsewhere = this.channelSlots.some(
            (slot) => slot.index !== currentSlotIndex && slot.enabled && slot.name === name
        );
        return activeElsewhere ? "active elsewhere" : "";
    }

    setSlotRange(slotIndex, values, userChanged = false) {
        const slot = this.channelSlots[slotIndex];
        if (!slot) return;
        slot.range = this.normalizeRange(values, true);
        if (userChanged) {
            slot.userRangeChanged = true;
            if (slot.name) {
                this.markerRangeOverrides.set(slot.name, [...slot.range]);
            }
        }
        this.channelList.image_channels[slot.name] = slot.range;
        this.updateSlotReadout(slot);
        if (slot.enabled && slot.name) {
            this.eventHandler.trigger(ChannelList.events.BRUSH_MOVE, {
                name: slot.name,
                dataRange: [...slot.range],
            });
        }
    }

    autoLevelChannelIfNeeded(slot) {
        if (!slot || slot.autoLeveled || slot.autoLeveling || slot.userRangeChanged) {
            return;
        }
        window.setTimeout(() => this.autoChannel(slot.index), 0);
    }

    async autoChannel(slotIndex, options = {}) {
        const slot = this.channelSlots[slotIndex];
        if (!slot || !slot.name) return;
        if (!options.force && (slot.userRangeChanged || slot.autoLeveled || slot.autoLeveling)) {
            return;
        }
        const markerName = slot.name;
        slot.autoLeveling = true;
        if (!(slot.name in this.channelList.hasChannelGMM)) {
            await this.channelList.getAndDrawChannelGMM(slot.name);
        }
        slot.autoLeveling = false;
        if (slot.name !== markerName) {
            return;
        }
        const packet = this.channelList.hasChannelGMM[slot.name];
        if (!packet || (!options.force && slot.userRangeChanged)) return;
        slot.range = [packet.vmin, packet.vmax];
        slot.autoLeveled = true;
        const slider = this.channelSlotSliders.get(slotIndex);
        if (slider) {
            slider.silentValue(slot.range);
        }
        this.setSlotRange(slotIndex, slot.range, false);
        this.redrawChannelSlider(slot);
    }

    addFirstAvailableChannel() {
        const emptySlot = this.channelSlots.find((slot) => !slot.visible);
        const activeNames = this.channelSlots.filter((slot) => slot.enabled).map((slot) => slot.name);
        const usedNames = this.channelSlots.filter((slot) => slot.name).map((slot) => slot.name);
        const slot = emptySlot || this.createAdditionalSlot(usedNames);
        if (!slot) return;
        const next = slot.name && !activeNames.includes(slot.name)
            ? slot.name
            : this.columns.find((name) => !usedNames.includes(name)) || this.columns.find((name) => !activeNames.includes(name));
        if (next) {
            this.setSlotMarker(slot.index, next, { keepColor: true, enable: false, reveal: true });
        }
    }

    createAdditionalSlot(usedNames) {
        if (this.channelSlots.length >= this.maxChannelSlots) return null;
        const slotIndex = this.channelSlots.length;
        const color = this.getDefaultColor(slotIndex);
        const name = this.columns.find((column) => !usedNames.includes(column)) || "";
        const slot = {
            index: slotIndex,
            name,
            color: color.rgb,
            colorHex: color.hex,
            enabled: false,
            visible: true,
            expanded: false,
            sliderDirty: false,
            range: this.getImageRange(name),
            userColorChanged: false,
            userRangeChanged: false,
            autoLeveled: false,
            autoLeveling: false,
        };
        this.channelSlots.push(slot);
        document.getElementById("channel_slot_list").appendChild(this.createChannelSlot(slot));
        return slot;
    }

    removeChannelSlot(slotIndex) {
        const slot = this.channelSlots[slotIndex];
        if (!slot) return;
        if (slot.enabled && slot.name) {
            this.deactivateChannel(slot);
        }
        const color = this.getDefaultColor(slotIndex);
        slot.name = "";
        slot.range = this.getImageRange("");
        slot.enabled = false;
        slot.visible = false;
        slot.expanded = false;
        slot.sliderDirty = false;
        slot.color = color.rgb;
        slot.colorHex = color.hex;
        slot.userColorChanged = false;
        slot.userRangeChanged = false;
        slot.autoLeveled = false;
        slot.autoLeveling = false;
        this.channelSlotSliders.delete(slotIndex);
        this.syncSlotDom(slot);
        this.applySlotExpansion(slot);
        this.updateSelectedCount();
        this.scheduleSaveChannels();
    }

    syncSlotDom(slot) {
        const row = document.querySelector(`.channel-slot[data-slot="${slot.index}"]`);
        if (!row) return;
        row.classList.toggle("is-hidden", !slot.visible);
        row.classList.toggle("is-disabled", !slot.enabled);
        row.style.setProperty("--slot-color", slot.colorHex);
        const toggle = row.querySelector(".channel-toggle-switch");
        if (toggle) toggle.checked = slot.enabled;
        const colorPicker = this.colorPickers.get(slot.index);
        if (colorPicker) colorPicker.setValue(slot.colorHex);
        const markerSelect = this.markerSelects.get(slot.index);
        if (markerSelect) markerSelect.setValue(slot.name);
        this.updateSlotReadout(slot);
    }

    applySavedGating(rows) {
        let activeRow = null;
        rows.forEach((row) => {
            if (!row || !row.channel || row.channel === "Lasso") return;
            this.gatingList.gating_channels[row.channel] = [row.gate_start, row.gate_end];
            if (row.gate_active) {
                activeRow = row;
            }
        });
        const marker = activeRow
            ? this.dataLayer.getShortChannelName(activeRow.channel)
            : (this.columns[1] || this.columns[0]);
        // syncSlot:false - applySavedChannels already placed every active channel (including
        // this one, if it was active) in its correct restored slot; letting setGateMarker's
        // normal slot-1 mirroring run here would clobber whatever channel actually belongs there.
        this.setGateMarker(marker, { force: true, syncSlot: false });
    }

    applySavedChannels(rows) {
        const activeRows = rows.filter((row) => row && row.channel_active);
        const slotList = document.getElementById("channel_slot_list");
        slotList.innerHTML = "";
        this.channelSlots = [];
        this.channelSlotSliders.clear();
        this.colorPickers.clear();
        this.markerSelects.clear();

        const count = Math.min(Math.max(activeRows.length, this.initialChannelSlots), this.maxChannelSlots);
        // Slots beyond the active rows aren't assigned by a saved row at all, but they should
        // still show a marker (off) rather than sit empty, matching the pre-restore default look.
        const usedNames = new Set(activeRows.slice(0, count).map((row) => row.channel));
        const fallbackNames = this.columns.filter((name) => !usedNames.has(name));
        let fallbackIdx = 0;
        for (let i = 0; i < count; i++) {
            const color = this.getDefaultColor(i);
            const name = i < activeRows.length ? "" : (fallbackNames[fallbackIdx++] || "");
            const slot = {
                index: i,
                name,
                color: color.rgb,
                colorHex: color.hex,
                enabled: false,
                visible: true,
                expanded: false,
                sliderDirty: false,
                range: this.getImageRange(name),
                userColorChanged: false,
                userRangeChanged: false,
                autoLeveled: false,
                autoLeveling: false,
            };
            this.channelSlots.push(slot);
            slotList.appendChild(this.createChannelSlot(slot));
        }

        activeRows.slice(0, count).forEach((row, i) => {
            const slot = this.channelSlots[i];
            if (!slot) return;
            this.setSlotMarker(slot.index, row.channel, { keepColor: true, enable: true, force: true });
            this.setSlotColor(slot.index, this.rgbToHex(row.r, row.g, row.b), true);
            this.setSlotRange(slot.index, [row.start, row.end], true);
            slot.expanded = false;
            this.applySlotExpansion(slot);
        });
        this.updateSelectedCount();
    }

    rgbToHex(r, g, b) {
        const toHex = (value) => Math.max(0, Math.min(255, Math.round(value))).toString(16).padStart(2, "0");
        return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
    }

    persistChannelList() {
        const listChannels = {};
        this.columns.forEach((name) => {
            listChannels[name] = this.channelList.image_channels[name] || this.getImageRange(name);
        });
        const activeChannels = {};
        const listColors = {};
        const listRanges = {};
        this.channelSlots.forEach((slot) => {
            if (!slot.name || !slot.enabled) return;
            const fullName = this.dataLayer.getFullChannelName(slot.name);
            const idx = imageChannels[fullName];
            if (idx === undefined) return;
            activeChannels[idx] = true;
            listColors[idx] = { color: { ...slot.color, opacity: 1 } };
            listRanges[idx] = this.toImageConnectorRange(slot.range);
            listChannels[slot.name] = slot.range;
        });
        return this.dataLayer.saveChannelList(imageChannelsIdx, activeChannels, listColors, listRanges, listChannels);
    }

    persistGatingList() {
        return this.dataLayer.saveGatingList(this.gatingList.gating_channels, this.gatingList.selections, {});
    }

    scheduleSaveChannels() {
        if (this._restoring) return;
        window.clearTimeout(this._saveChannelsTimer);
        this._saveChannelsTimer = window.setTimeout(() => {
            // Chained (not just debounced): a slow/out-of-order response from an earlier
            // save could otherwise land after a newer one and silently overwrite it.
            this._channelSaveChain = (this._channelSaveChain || Promise.resolve()).then(() => this.persistChannelList());
        }, 400);
    }

    scheduleSaveGating() {
        if (this._restoring) return;
        window.clearTimeout(this._saveGatingTimer);
        this._saveGatingTimer = window.setTimeout(() => {
            this._gatingSaveChain = (this._gatingSaveChain || Promise.resolve()).then(() => this.persistGatingList());
        }, 400);
    }

    updateSelectedCount() {
        const count = this.channelSlots.filter((slot) => slot.enabled && slot.name).length;
        const countElement = document.getElementById("num-selected-channels");
        if (countElement) countElement.textContent = count;
        const addButton = document.getElementById("add_channel_button");
        if (addButton) addButton.disabled = this.channelSlots.filter((slot) => slot.visible).length >= this.maxChannelSlots;
    }

    updateGateReadout(values) {
        document.getElementById("gate_min_value").textContent = this.formatValue(values[0]);
        document.getElementById("gate_max_value").textContent = this.formatValue(values[1]);
    }

    updateSlotReadout(slot) {
        const min = document.getElementById(`channel_slot_min_${slot.index}`);
        const max = document.getElementById(`channel_slot_max_${slot.index}`);
        if (min) min.textContent = this.formatValue(slot.range[0]);
        if (max) max.textContent = this.formatValue(slot.range[1]);
    }

    getGateRange(name) {
        const fullName = this.dataLayer.getFullChannelName(name);
        const desc = this.databaseDescription[fullName] || {};
        return [desc.min || 0, desc.max || 1];
    }

    getImageRange(name) {
        if (!name) return [0, 1];
        const fullName = this.dataLayer.getFullChannelName(name);
        const desc = this.databaseDescription[fullName] || {};
        return [desc.image_min || this.dataLayer.imageBitRange[0] || 0, desc.image_max || this.dataLayer.imageBitRange[1] || 65536];
    }

    toImageConnectorRange(values) {
        const defaultRange = this.dataLayer.imageBitRange;
        return [values[0] / defaultRange[1], values[1] / defaultRange[1]];
    }

    normalizeRange(values, keepFloat) {
        const sorted = [...values].map((value) => parseFloat(value)).sort((a, b) => a - b);
        if (keepFloat) {
            return sorted;
        }
        return [Math.floor(sorted[0]), Math.ceil(sorted[1])];
    }

    formatValue(value) {
        return Number.parseFloat(value || 0).toFixed(2);
    }

    hexToRgb(hex) {
        const cleaned = hex.replace("#", "");
        const value = parseInt(cleaned, 16);
        return {
            r: (value >> 16) & 255,
            g: (value >> 8) & 255,
            b: value & 255,
        };
    }

    getDefaultColor(slotIndex) {
        return this.defaultColors[slotIndex % this.defaultColors.length];
    }
}
