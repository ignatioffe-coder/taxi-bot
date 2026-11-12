(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  const $ = (id) => document.getElementById(id);
  const currency = new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  });
  const hasTelegramAuth = Boolean(tg && tg.initData);
  let workType = "taxi";
  let period = "week";
  let districtGroups = [];
  let districtCenters = {};
  let districtBoundaries = {};
  let yandexMapsApiKey = null;
  let yandexMapsPromise = null;
  let coefficientMap = null;
  let coefficientMapHasInitialBounds = false;
  let coefficientAreas = null;
  let coefficientMapDataSignature = null;
  let preferredName = "";
  let activeMapTouches = 0;
  let viewportFitPending = false;
  let viewportFitTimer = null;

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor("#11151b");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#11151b");
    if (tg.onEvent) {
      tg.onEvent("viewportChanged", () => {
        scheduleMapViewportFit();
      });
    }
  }
  const headers = hasTelegramAuth
    ? { "X-Telegram-Init-Data": tg.initData }
    : {};

  function showToast(message) {
    const toast = $("toast");
    toast.textContent = message;
    toast.classList.add("visible");
    window.setTimeout(() => toast.classList.remove("visible"), 3000);
  }

  function showError(error) {
    if (error.status === 401) {
      showToast(hasTelegramAuth
        ? "Telegram не смог подтвердить сессию. Проверь BOT_TOKEN в BotHost и перезапусти бота."
        : "Telegram не передал данные. Отправь боту /start и открой Mini App кнопкой под новым сообщением.");
    } else {
      showToast(error.message || "Не удалось выполнить запрос. Попробуй ещё раз.");
    }
  }

  async function api(path, options = {}) {
    if (!hasTelegramAuth) {
      const error = new Error("Telegram authorization is required");
      error.status = 401;
      throw error;
    }
    const response = await fetch(path, {
      ...options,
      headers: { ...headers, ...(options.headers || {}) },
    });
    if (!response.ok) {
      const error = new Error(
        response.status === 400
          ? "Проверь данные и обязательные поля."
          : response.status === 403
            ? "Сначала заполни профиль: укажи имя и вид работы."
          : "Сервис временно недоступен. Попробуй ещё раз.",
      );
      error.status = response.status;
      throw error;
    }
    return response;
  }

  async function loadDistrictCatalog() {
    const response = await fetch("/api/districts");
    if (!response.ok) throw new Error("Не удалось загрузить список районов.");
    const data = await response.json();
    renderDistricts(data.district_groups);
    await loadMapResources();
  }

  async function loadMapResources() {
    try {
      const [centersResponse, boundariesResponse, mapConfigResponse] = await Promise.all([
        fetch("/static/district-centers.json"),
        fetch("/static/district-boundaries.json"),
        fetch("/api/map-config"),
      ]);
      if (!centersResponse.ok || !boundariesResponse.ok || !mapConfigResponse.ok) {
        throw new Error("Не удалось загрузить настройки карты.");
      }
      const [mapData, boundaries, mapConfig] = await Promise.all([
        centersResponse.json(),
        boundariesResponse.json(),
        mapConfigResponse.json(),
      ]);
      districtCenters = mapData.districts || {};
      districtBoundaries = boundaries.districts || {};
      yandexMapsApiKey = mapConfig.yandex_maps_api_key || "";
      renderCoefficientMap(districtGroups);
    } catch (error) {
      showMapError(
        "Не удалось загрузить данные карты. Проверь интернет и попробуй снова.",
        () => loadMapResources().catch(showError),
      );
      throw error;
    }
  }

  async function refreshMarketStats() {
    const response = await fetch("/api/districts", { cache: "no-store" });
    if (!response.ok) throw new Error("Не удалось обновить коэффициенты районов.");
    const data = await response.json();
    renderDistricts(data.district_groups);
  }

  function setView(view) {
    document.querySelectorAll(".view").forEach((section) => {
      section.classList.toggle("active", section.id === `view-${view}`);
    });
    document.querySelectorAll(".nav-button").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === view);
    });
    if (view === "districts") {
      window.requestAnimationFrame(() => {
        renderCoefficientMap(districtGroups, true);
        window.setTimeout(fitCoefficientMapToViewport, 250);
      });
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function fitCoefficientMapToViewport() {
    if (
      coefficientMap
      && document.querySelector("#view-districts").classList.contains("active")
    ) {
      coefficientMap.container.fitToViewport();
    }
  }

  function scheduleMapViewportFit() {
    viewportFitPending = true;
    window.clearTimeout(viewportFitTimer);
    viewportFitTimer = window.setTimeout(() => {
      if (activeMapTouches) return;
      viewportFitPending = false;
      fitCoefficientMapToViewport();
    }, 300);
  }

  const mapContainer = $("coefficient-map");
  mapContainer.addEventListener("touchstart", (event) => {
    activeMapTouches = event.touches.length;
    window.clearTimeout(viewportFitTimer);
  }, { passive: true });
  const finishMapTouch = (event) => {
    activeMapTouches = event.touches.length;
    if (!activeMapTouches && viewportFitPending) scheduleMapViewportFit();
  };
  mapContainer.addEventListener("touchend", finishMapTouch, { passive: true });
  mapContainer.addEventListener("touchcancel", finishMapTouch, { passive: true });

  function updateWorkTypeButtons() {
    document.querySelectorAll("[data-work-type]").forEach((button) => {
      button.classList.toggle("active", button.dataset.workType === workType);
    });
    $("profile-work-type").value = workType;
  }

  function setWorkType(value) {
    workType = value;
    updateWorkTypeButtons();
    if (hasTelegramAuth) loadDashboard().catch(showError);
  }

  function setPeriod(value) {
    period = value;
    document.querySelectorAll(".period-button").forEach((button) => {
      button.classList.toggle("active", button.dataset.period === value);
    });
    if (hasTelegramAuth) loadDashboard().catch(showError);
  }

  function fillDistrictSelect(select, placeholder, selectedValue = "") {
    select.replaceChildren(new Option(placeholder, ""));
    districtGroups.forEach((group) => {
      const optgroup = document.createElement("optgroup");
      optgroup.label = group.name;
      group.districts.forEach((district) => {
        optgroup.appendChild(new Option(district.name, district.name));
      });
      select.appendChild(optgroup);
    });
    select.value = selectedValue;
  }

  function renderDistricts(groups) {
    districtGroups = groups;
    const container = $("district-groups");
    container.replaceChildren();

    groups.forEach((group, index) => {
      const details = document.createElement("details");
      details.className = "district-group";
      details.open = index === 0;

      const summary = document.createElement("summary");
      const title = document.createElement("span");
      title.className = "okrug-title";
      title.textContent = group.name;
      const meta = document.createElement("span");
      meta.className = "okrug-meta";
      meta.textContent = `${group.districts.length} районов`;
      summary.append(title, meta);

      const rows = document.createElement("div");
      rows.className = "district-rows";
      group.districts.forEach((district) => {
        const row = document.createElement("div");
        row.className = "district-row";
        const name = document.createElement("span");
        name.className = "district-name";
        name.textContent = district.name;
        const statistic = document.createElement("span");
        statistic.className = "district-stat";
        statistic.textContent = district.records
          ? `${district.avg_coefficient.toFixed(1)}x · ${district.records} зам. · ${formatCoefficientTime(district.last_update)}`
          : "пока без замеров";
        if (district.records) statistic.classList.add("has-data");
        row.append(name, statistic);
        rows.appendChild(row);
      });

      details.append(summary, rows);
      container.appendChild(details);
    });
    const empty = document.createElement("p");
    empty.className = "empty-note district-empty";
    empty.textContent = "Не нашёлся район. Попробуй другое название.";
    empty.hidden = true;
    container.appendChild(empty);

    const selectedCoefficientDistrict = $("coefficient-district").value;
    const selectedShiftDistrict = $("shift-district").value;
    fillDistrictSelect(
      $("coefficient-district"),
      "Выбери район",
      selectedCoefficientDistrict,
    );
    fillDistrictSelect(
      $("shift-district"),
      "Не указывать",
      selectedShiftDistrict,
    );
    applyDistrictSearch();
    renderCoefficientMap(groups);
  }

  function coefficientColor(value, hasData) {
    if (!hasData) return "#87939e";
    if (value >= 2.5) return "#d781ff";
    if (value >= 2) return "#ff8176";
    if (value >= 1.6) return "#ffb84c";
    if (value >= 1.3) return "#f3d16b";
    return "#7ce0b1";
  }

  function toYandexCoordinates(geometry) {
    const convertRing = (ring) => ring.map(([longitude, latitude]) => [latitude, longitude]);
    if (geometry.type === "Polygon") {
      return geometry.coordinates.map(convertRing);
    }
    return geometry.coordinates.map((polygon) => polygon.map(convertRing));
  }

  function showMapError(text, retryAction = () => renderCoefficientMap(districtGroups)) {
    $("map-report-count").textContent = "Карта недоступна";
    const message = $("map-message");
    message.classList.add("map-error");
    message.replaceChildren();
    const explanation = document.createElement("span");
    explanation.textContent = text;
    const retry = document.createElement("button");
    retry.className = "map-retry";
    retry.type = "button";
    retry.textContent = "Повторить";
    retry.addEventListener("click", retryAction, { once: true });
    message.append(explanation, retry);
  }

  function loadYandexMaps() {
    if (yandexMapsPromise) return yandexMapsPromise;
    const script = document.createElement("script");
    const url = new URL("https://api-maps.yandex.ru/2.1/");
    url.searchParams.set("apikey", yandexMapsApiKey);
    url.searchParams.set("lang", "ru_RU");
    script.src = url.toString();
    yandexMapsPromise = new Promise((resolve, reject) => {
      const timeoutId = window.setTimeout(() => {
        script.remove();
        reject(new Error("Timed out loading Yandex Maps API"));
      }, 15000);
      const fail = (error) => {
        window.clearTimeout(timeoutId);
        script.remove();
        reject(error);
      };
      script.addEventListener("load", () => {
        if (!window.ymaps) {
          fail(new Error("Yandex Maps API did not initialize"));
          return;
        }
        window.ymaps.ready(() => {
          window.clearTimeout(timeoutId);
          resolve(window.ymaps);
        });
      }, { once: true });
      script.addEventListener(
        "error",
        () => fail(new Error("Could not load Yandex Maps API")),
        { once: true },
      );
      document.head.appendChild(script);
    }).catch((error) => {
      yandexMapsPromise = null;
      throw error;
    });
    return yandexMapsPromise;
  }

  function renderCoefficientMap(groups, force = false) {
    const badge = $("map-report-count");
    if (yandexMapsApiKey === null) return;
    if (!yandexMapsApiKey) {
      badge.textContent = "Нужен API-ключ";
      $("map-message").textContent = "Добавь YANDEX_MAPS_API_KEY в переменные окружения и ограничь ключ доменом Mini App.";
      return;
    }
    if (!document.querySelector("#view-districts").classList.contains("active")) {
      return;
    }
    if (!Object.keys(districtCenters).length) return;

    const districts = groups.flatMap((group) => group.districts);
    const reportingDistricts = districts.filter((district) => district.records > 0).length;
    const dataSignature = JSON.stringify(districts.map((district) => [
      district.name,
      district.records,
      district.avg_coefficient,
      district.last_update,
    ]));
    badge.textContent = "Загружаем карту…";
    $("map-message").textContent = "";

    loadYandexMaps().then((ymaps) => {
      if (!document.querySelector("#view-districts").classList.contains("active")) {
        return;
      }
      if (!coefficientMap) {
        coefficientMap = new ymaps.Map("coefficient-map", {
          center: [55.751, 37.617],
          zoom: 8,
          controls: ["zoomControl"],
          behaviors: ["drag", "multiTouch"],
        });
        coefficientAreas = new ymaps.GeoObjectCollection();
        coefficientMap.geoObjects.add(coefficientAreas);
      }
      if (!force && dataSignature === coefficientMapDataSignature) {
        badge.textContent = `${reportingDistricts} из ${districts.length} с данными`;
        window.requestAnimationFrame(fitCoefficientMapToViewport);
        return;
      }
      coefficientAreas.removeAll();
      const bounds = [[], []];

      districts.forEach((district) => {
        const center = districtCenters[district.name];
        const boundary = districtBoundaries[district.name];
        if (!center || !boundary) return;
        const coordinates = [center.lat, center.lon];
        bounds[0][0] = bounds[0][0] === undefined
          ? coordinates[0] : Math.min(bounds[0][0], coordinates[0]);
        bounds[0][1] = bounds[0][1] === undefined
          ? coordinates[1] : Math.min(bounds[0][1], coordinates[1]);
        bounds[1][0] = bounds[1][0] === undefined
          ? coordinates[0] : Math.max(bounds[1][0], coordinates[0]);
        bounds[1][1] = bounds[1][1] === undefined
          ? coordinates[1] : Math.max(bounds[1][1], coordinates[1]);

        const hasData = district.records > 0;
        const color = coefficientColor(district.avg_coefficient, hasData);
        const sampleCount = Number(district.records) || 0;
        const reportedAt = formatCoefficientTime(district.last_update);
        const balloonBody = hasData
          ? `Средний коэффициент: <strong>${Number(district.avg_coefficient).toFixed(1)}x</strong><br>${sampleCount} ${plural(sampleCount, "замер", "замера", "замеров")}<br>Последний замер: ${reportedAt}`
          : "Пока нет замеров от водителей";
        coefficientAreas.add(new ymaps.GeoObject({
          geometry: {
            type: boundary.type,
            coordinates: toYandexCoordinates(boundary),
          },
          properties: {
            balloonContentHeader: district.name,
            balloonContentBody: balloonBody,
            hintContent: hasData
              ? `${district.name} · ${Number(district.avg_coefficient).toFixed(1)}x · ${reportedAt}`
              : `${district.name} · нет замеров`,
          },
        }, {
          fillColor: color,
          fillOpacity: hasData ? 0.45 : 0.06,
          strokeColor: hasData ? color : "#87939e",
          strokeOpacity: 0.9,
          strokeWidth: 1,
        }));
      });

      if (bounds[0].length && !coefficientMapHasInitialBounds) {
        coefficientMap.setBounds(bounds, { checkZoomRange: true, zoomMargin: 20 });
        coefficientMapHasInitialBounds = true;
      }
      coefficientMapDataSignature = dataSignature;
      badge.textContent = `${reportingDistricts} из ${districts.length} с данными`;
      const message = $("map-message");
      message.classList.remove("map-error");
      message.replaceChildren();
      window.requestAnimationFrame(fitCoefficientMapToViewport);
    }).catch(() => {
      showMapError("Не удалось загрузить карту. Проверь интернет, VPN или домен API-ключа Яндекса.");
    });
  }

  function applyDistrictSearch() {
    const query = $("district-search").value.trim().toLocaleLowerCase("ru");
    let visibleGroups = 0;
    document.querySelectorAll(".district-group").forEach((group) => {
      const titleMatches = group.querySelector(".okrug-title").textContent
        .toLocaleLowerCase("ru").includes(query);
      let visibleRows = 0;
      group.querySelectorAll(".district-row").forEach((row) => {
        const matches = !query || titleMatches
          || row.querySelector(".district-name").textContent
            .toLocaleLowerCase("ru").includes(query);
        row.hidden = !matches;
        if (matches) visibleRows += 1;
      });
      group.hidden = visibleRows === 0;
      if (visibleRows) visibleGroups += 1;
      if (query && visibleRows) group.open = true;
    });
    const empty = document.querySelector(".district-empty");
    if (empty) empty.hidden = visibleGroups > 0;
  }

  function renderRecommendations(items) {
    const container = $("recommendations");
    container.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "empty-note";
      empty.textContent = "Пока недостаточно замеров для прогноза. Добавь коэффициент в разделе «Районы».";
      container.appendChild(empty);
      return;
    }

    items.forEach((item, index) => {
      const card = document.createElement("article");
      card.className = "recommendation";
      const rank = document.createElement("span");
      rank.className = "recommendation-rank";
      rank.textContent = String(index + 1);
      const info = document.createElement("div");
      const name = document.createElement("p");
      name.className = "recommendation-name";
      name.textContent = item.district;
      const note = document.createElement("span");
      note.className = "recommendation-note";
      note.textContent = `${item.data_points} замеров · уверенность ${item.reliability}%`;
      info.append(name, note);
      const coefficient = document.createElement("strong");
      coefficient.className = "recommendation-coef";
      coefficient.textContent = `${item.avg_coefficient.toFixed(1)}x`;
      card.append(rank, info, coefficient);
      container.appendChild(card);
    });
  }

  function renderChart(chart) {
    const container = $("week-chart");
    container.replaceChildren();
    const max = Math.max(1, ...chart.map((day) => Math.abs(day.net_income)));
    chart.forEach((day) => {
      const column = document.createElement("div");
      column.className = "chart-day";
      const value = document.createElement("span");
      value.className = "chart-value";
      value.textContent = day.net_income ? compactMoney(day.net_income) : "—";
      const track = document.createElement("div");
      track.className = "chart-track";
      const bar = document.createElement("div");
      bar.className = `chart-bar${day.net_income < 0 ? " negative" : ""}`;
      bar.style.height = `${Math.max(4, Math.abs(day.net_income) / max * 48)}px`;
      bar.title = currency.format(day.net_income);
      track.appendChild(bar);
      const label = document.createElement("span");
      label.className = "chart-label";
      label.textContent = new Intl.DateTimeFormat("ru-RU", { weekday: "short" })
        .format(new Date(`${day.date}T12:00:00`)).replace(".", "");
      column.append(value, track, label);
      container.appendChild(column);
    });
  }

  function renderExpenseBreakdown(expenses, total) {
    const container = $("expense-breakdown");
    container.replaceChildren();
    const labels = [
      ["fuel_cost", "Топливо"],
      ["commission_cost", "Комиссии"],
      ["maintenance_cost", "Обслуживание"],
      ["other_cost", "Другие"],
    ];
    labels.forEach(([key, label]) => {
      const amount = expenses[key] || 0;
      const row = document.createElement("div");
      row.className = "expense-row";
      const meta = document.createElement("div");
      meta.className = "expense-meta";
      const name = document.createElement("span");
      name.textContent = label;
      const value = document.createElement("strong");
      value.textContent = currency.format(amount);
      meta.append(name, value);
      const track = document.createElement("div");
      track.className = "expense-track";
      const fill = document.createElement("div");
      fill.className = "expense-fill";
      fill.style.width = `${total ? Math.min(amount / total * 100, 100) : 0}%`;
      track.appendChild(fill);
      row.append(meta, track);
      container.appendChild(row);
    });
  }

  function compactMoney(amount) {
    const abs = Math.abs(amount);
    const prefix = amount < 0 ? "−" : "";
    if (abs >= 10000) return `${prefix}${(abs / 1000).toFixed(0)}к`;
    if (abs >= 1000) return `${prefix}${(abs / 1000).toFixed(1)}к`;
    return `${prefix}${Math.round(abs)}`;
  }

  function renderShifts(container, shifts, emptyMessage) {
    container.replaceChildren();
    if (!shifts.length) {
      const empty = document.createElement("p");
      empty.className = "empty-note";
      empty.textContent = emptyMessage;
      container.appendChild(empty);
      return;
    }

    shifts.forEach((shift) => {
      const expenses = Number(shift.fuel_cost) + Number(shift.commission_cost)
        + Number(shift.maintenance_cost) + Number(shift.other_cost);
      const net = Number(shift.gross_income) + Number(shift.tips) - expenses;
      const row = document.createElement("article");
      row.className = "shift-row";
      const info = document.createElement("div");
      info.className = "shift-info";
      const title = document.createElement("p");
      title.className = "shift-title";
      title.textContent = shift.district || "Смена без района";
      const subtitle = document.createElement("span");
      subtitle.className = "shift-subtitle";
      subtitle.textContent = `${formatDate(shift.timestamp)} · ${shift.hours_worked || 0} ч`;
      info.append(title, subtitle);
      const money = document.createElement("div");
      money.className = "shift-money";
      const netValue = document.createElement("strong");
      netValue.className = `shift-net ${net >= 0 ? "positive" : "negative"}`;
      netValue.textContent = currency.format(net);
      const gross = document.createElement("span");
      gross.className = "shift-gross";
      gross.textContent = `доходы ${currency.format(Number(shift.gross_income) + Number(shift.tips))}`;
      money.append(netValue, gross);
      row.append(info, money);
      container.appendChild(row);
    });
  }

  function formatDate(value) {
    const date = new Date(value.replace(" ", "T") + "+03:00");
    return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short" }).format(date);
  }

  function formatCoefficientTime(value) {
    if (!value) return "время неизвестно";
    const date = new Date(value.replace(" ", "T") + "+03:00");
    if (Number.isNaN(date.getTime())) return "время неизвестно";
    return new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function updateShiftPreview() {
    const read = (name) => Number($(`shift-form`).elements[name].value || 0);
    const income = read("gross_income") + read("tips");
    const expenses = read("fuel_cost") + read("commission_cost")
      + read("maintenance_cost") + read("other_cost");
    const net = income - expenses;
    $("shift-net-preview").textContent = currency.format(net);
    $("shift-net-preview").style.color = net >= 0 ? "var(--mint)" : "var(--red)";
  }

  function renderDocumentStatuses(profile) {
    const container = $("document-status-list");
    container.replaceChildren();
    const documents = [
      ["medical_book_expiry", "Медицинская книжка"],
      ["license_expiry", "Водительское удостоверение"],
      ["osago_expiry", "ОСАГО"],
      ["inspection_expiry", "Техосмотр"],
      ["taxi_permit_expiry", "Разрешение такси"],
    ];
    const dateParts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Moscow",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    const today = Date.UTC(
      Number(dateParts.find((part) => part.type === "year").value),
      Number(dateParts.find((part) => part.type === "month").value) - 1,
      Number(dateParts.find((part) => part.type === "day").value),
    );

    documents.forEach(([field, label]) => {
      const row = document.createElement("div");
      row.className = "document-status-item";
      const name = document.createElement("strong");
      name.textContent = label;
      const status = document.createElement("span");
      const expiry = profile[field];
      if (!expiry) {
        status.textContent = "Дата не указана";
      } else {
        const days = Math.round((Date.parse(`${expiry}T00:00:00Z`) - today) / 86400000);
        if (days < 0) {
          row.classList.add("expired");
          status.textContent = `Просрочен · ${expiry.split("-").reverse().join(".")}`;
        } else if (days === 0) {
          row.classList.add("soon");
          status.textContent = "Истекает сегодня";
        } else {
          if (days <= 7) row.classList.add("soon");
          status.textContent = `${expiry.split("-").reverse().join(".")} · ${plural(days, "день", "дня", "дней")}`;
        }
      }
      row.append(name, status);
      container.appendChild(row);
    });
  }

  async function loadProfile() {
    const profile = await (await api("/api/profile")).json();
    const form = $("profile-form");
    const profileFields = [
      "work_type", "vehicle_type", "vehicle_make", "vehicle_model", "vehicle_year",
      "fuel_type", "weekly_income_goal", "medical_book_expiry", "license_expiry",
      "osago_expiry", "inspection_expiry", "taxi_permit_expiry",
    ];
    profileFields.forEach((field) => {
      form.elements[field].value = profile[field] ?? "";
    });
    form.elements.reminders_enabled.checked = profile.reminders_enabled;
    workType = profile.work_type;
    updateWorkTypeButtons();
    preferredName = profile.preferred_name || "";
    const fullName = preferredName
      || [profile.first_name, profile.last_name].filter(Boolean).join(" ")
      || "Водитель";
    $("profile-name").textContent = fullName;
    $("profile-avatar").textContent = Array.from(fullName.trim())[0] || "В";
    renderDocumentStatuses(profile);
    return profile;
  }

  async function loadDashboard() {
    const query = new URLSearchParams({ work_type: workType, period });
    const data = await (await api(`/api/dashboard?${query}`)).json();
    const name = preferredName || data.user.first_name || "водитель";
    $("driver-name").textContent = name;
    $("avatar").textContent = Array.from(name.trim())[0] || "В";

    const periodLabels = { day: "ИТОГ ЗА ДЕНЬ", week: "ИТОГ ЗА НЕДЕЛЮ", month: "ИТОГ ЗА МЕСЯЦ" };
    $("profit-period-label").textContent = periodLabels[period];
    $("net-income").textContent = currency.format(data.summary.net_income);
    $("net-income").classList.toggle("negative", data.summary.net_income < 0);
    $("shift-count").textContent = `${data.summary.shifts} ${plural(data.summary.shifts, "смена", "смены", "смен")}`;
    $("hourly-income").textContent = `${currency.format(data.summary.average_per_hour)}/ч`;
    $("gross-income").textContent = currency.format(data.summary.gross_income + data.summary.tips);
    $("total-expenses").textContent = currency.format(data.summary.total_expenses);
    $("hours-worked").textContent = `${data.summary.hours_worked} ч`;
    const weeklyGoal = data.weekly_goal;
    const weeklyTarget = Number(weeklyGoal.target) || 0;
    const weeklyNet = Number(weeklyGoal.net_income) || 0;
    const goalProgress = weeklyTarget > 0
      ? Math.max(0, Math.min(100, weeklyNet / weeklyTarget * 100))
      : 0;
    $("weekly-goal-title").textContent = weeklyTarget > 0
      ? `${currency.format(weeklyNet)} из ${currency.format(weeklyTarget)}`
      : "Цель не задана";
    $("weekly-goal-progress").style.width = `${goalProgress}%`;
    $("weekly-goal-caption").textContent = weeklyTarget > 0
      ? weeklyNet >= weeklyTarget
        ? "Цель недели достигнута — отличная работа!"
        : `До цели осталось ${currency.format(weeklyTarget - weeklyNet)}.`
      : "Укажи цель в профиле — приложение покажет прогресс.";
    renderChart(data.summary.chart);
    renderExpenseBreakdown(data.summary.expenses, data.summary.total_expenses);
    renderRecommendations(data.recommendations);
    renderShifts($("recent-shifts"), data.summary.recent_shifts, "За этот период смен ещё нет. Добавь первую в разделе «Смена».");
    renderShifts($("shift-history"), data.summary.recent_shifts, "За выбранный период смен ещё нет.");
    renderDistricts(data.district_groups);
  }

  function plural(count, one, few, many) {
    const lastTwo = count % 100;
    const last = count % 10;
    if (lastTwo >= 11 && lastTwo <= 14) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
  }

  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.querySelectorAll("[data-open-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.openView));
  });
  document.querySelectorAll("[data-work-type]").forEach((button) => {
    button.addEventListener("click", () => setWorkType(button.dataset.workType));
  });
  $("profile-work-type").addEventListener("change", (event) => {
    setWorkType(event.currentTarget.value);
  });
  document.querySelectorAll(".period-button").forEach((button) => {
    button.addEventListener("click", () => setPeriod(button.dataset.period));
  });
  $("refresh").addEventListener("click", () => {
    if (hasTelegramAuth) {
      loadDashboard().catch(showError);
    } else {
      showError({ status: 401 });
    }
  });
  $("district-search").addEventListener("input", applyDistrictSearch);
  $("shift-form").addEventListener("input", updateShiftPreview);

  $("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("save-profile");
    button.disabled = true;
    $("profile-message").textContent = "";
    const value = (name) => form.elements[name].value;
    const year = value("vehicle_year");
    const payload = {
      work_type: value("work_type"),
      vehicle_type: value("vehicle_type"),
      vehicle_make: value("vehicle_make"),
      vehicle_model: value("vehicle_model"),
      vehicle_year: year ? Number(year) : null,
      fuel_type: value("fuel_type"),
      weekly_income_goal: Number(value("weekly_income_goal") || 0),
      reminders_enabled: form.elements.reminders_enabled.checked,
      medical_book_expiry: value("medical_book_expiry"),
      license_expiry: value("license_expiry"),
      osago_expiry: value("osago_expiry"),
      inspection_expiry: value("inspection_expiry"),
      taxi_permit_expiry: value("taxi_permit_expiry"),
    };
    try {
      await api("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      $("profile-message").textContent = "Профиль сохранён.";
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      await loadProfile();
      await loadDashboard();
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });

  $("onboarding-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("complete-onboarding");
    const message = $("onboarding-message");
    button.disabled = true;
    message.textContent = "";
    try {
      await api("/api/onboarding", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: $("onboarding-name").value.trim(),
          work_type: form.elements.work_type.value,
        }),
      });
      const profile = await loadProfile();
      if (!profile.onboarding_complete) {
        throw new Error("Не удалось сохранить профиль. Попробуй ещё раз.");
      }
      await loadDashboard();
      $("onboarding-screen").hidden = true;
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      loadMapResources().catch(showError);
    } catch (error) {
      message.textContent = error.message || "Не удалось сохранить профиль. Попробуй ещё раз.";
      showError(error);
    } finally {
      button.disabled = false;
    }
  });

  $("coefficient-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    button.disabled = true;
    $("coefficient-message").textContent = "";
    try {
      await api("/api/coefficients", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          district: $("coefficient-district").value,
          coefficient: Number($("coefficient").value),
          weather: $("weather").value,
        }),
      });
      $("coefficient").value = "";
      $("coefficient-message").textContent = "Спасибо! Коэффициент добавлен.";
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      await loadDashboard();
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });

  $("shift-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("save-shift");
    button.disabled = true;
    $("shift-message").textContent = "";
    const form = event.currentTarget;
    const value = (name) => Number(form.elements[name].value || 0);
    try {
      await api("/api/shifts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          work_type: workType,
          district: $("shift-district").value || null,
          gross_income: value("gross_income"),
          tips: value("tips"),
          fuel_cost: value("fuel_cost"),
          commission_cost: value("commission_cost"),
          maintenance_cost: value("maintenance_cost"),
          other_cost: value("other_cost"),
          hours_worked: value("hours_worked"),
        }),
      });
      const selectedDistrict = $("shift-district").value;
      form.reset();
      $("shift-district").value = selectedDistrict;
      updateShiftPreview();
      $("shift-message").textContent = "Смена сохранена. Хорошая работа!";
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      await loadDashboard();
      setView("overview");
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });

  const now = new Date();
  $("today-label").textContent = new Intl.DateTimeFormat("ru-RU", {
    weekday: "long", day: "numeric", month: "long",
  }).format(now).toLocaleUpperCase("ru");
  if (hasTelegramAuth) {
    const onboardingScreen = $("onboarding-screen");
    const onboardingForm = $("onboarding-form");
    onboardingScreen.hidden = false;
    onboardingForm.hidden = true;
    $("onboarding-message").textContent = "Проверяем профиль…";
    (async () => {
      try {
        const profile = await loadProfile();
        if (!profile.onboarding_complete) {
          onboardingForm.hidden = false;
          $("onboarding-title").textContent = "Расскажи о себе";
          $("onboarding-intro").textContent = "Заполни профиль один раз, чтобы открыть Taxi Forecast.";
          $("onboarding-message").textContent = "";
          $("onboarding-name").focus();
          return;
        }
        await loadDashboard();
        onboardingScreen.hidden = true;
        loadMapResources().catch(showError);
      } catch (error) {
        $("onboarding-message").textContent = "Не удалось проверить профиль. Обнови приложение.";
        showError(error);
      }
    })();
  } else {
    const notice = '<p class="empty-note">Telegram не передал данные авторизации. Отправь боту /start и открой Mini App кнопкой под новым сообщением.</p>';
    $("recommendations").innerHTML = notice;
    $("district-groups").innerHTML = notice;
    $("week-chart").innerHTML = notice;
    $("expense-breakdown").innerHTML = notice;
    $("recent-shifts").innerHTML = notice;
    $("shift-history").innerHTML = notice;
    loadDistrictCatalog().catch(showError);
  }
  const refreshVisibleMarketStats = () => {
    if (!document.hidden) refreshMarketStats().catch(showError);
  };
  window.setInterval(refreshVisibleMarketStats, 60_000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshVisibleMarketStats();
      if (document.querySelector("#view-districts").classList.contains("active")) {
        window.setTimeout(() => {
          renderCoefficientMap(districtGroups, true);
          fitCoefficientMapToViewport();
        }, 250);
      }
    }
  });
})();
