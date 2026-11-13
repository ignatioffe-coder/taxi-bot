(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  const $ = (id) => document.getElementById(id);
  const currency = new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  });
  const reportCategoryLabels = {
    high_demand: "Высокий спрос",
    low_demand: "Мало заказов",
    airport_queue: "Очередь в аэропорту",
    few_cars: "Мало машин",
    low_activity: "Низкая активность",
    many_orders: "Много заказов",
    long_wait: "Долгое ожидание",
    restaurant_busy: "Задержка в ресторане",
    order_problem: "Проблема с заказом",
    delivery_problem: "Проблема с доставкой",
    traffic: "Пробка",
    accident: "ДТП",
    road_closure: "Перекрытие дороги",
  };
  const reportCategories = {
    taxi: [
      "high_demand", "low_demand", "airport_queue", "few_cars",
      "traffic", "accident", "road_closure",
    ],
    courier: [
      "high_demand", "low_activity", "many_orders", "long_wait",
      "restaurant_busy", "order_problem", "delivery_problem",
      "traffic", "accident", "road_closure",
    ],
  };
  const hasTelegramAuth = Boolean(tg && tg.initData);
  let workType = "taxi";
  let availableWorkTypes = [];
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
  let coefficientMapViewportSize = "";
  let preferredName = "";
  let currentSubscription = null;

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor("#11151b");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#11151b");
    if (tg.onEvent) {
      tg.onEvent("viewportChanged", () => {
        fitCoefficientMapToViewportIfResized();
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

  function showSubscriptionScreen(subscription = currentSubscription) {
    const access = subscription || {};
    const title = $("subscription-title");
    const status = $("subscription-status");
    $("subscription-price-amount").textContent = `${access.price_stars || 300} ⭐`;
    $("subscription-period-label").textContent =
      `за ${access.period_days || 30} дней · автопродление`;
    if (access.status === "paid" && access.paid_until) {
      title.textContent = "Подписка активна";
      status.textContent = `Доступ открыт до ${new Date(access.paid_until * 1000).toLocaleDateString("ru-RU")}.`;
    } else if (access.status === "trial" && access.trial_expires_at) {
      title.textContent = "Оплатить подписку сейчас";
      status.textContent = `Пробный период бесплатный до ${new Date(access.trial_expires_at * 1000).toLocaleDateString("ru-RU")}. При оплате сейчас 30-дневная подписка начнётся сразу.`;
    } else if (access.trial_expires_at) {
      title.textContent = "Пробный период закончился";
      status.textContent = `Бесплатный период завершился ${new Date(access.trial_expires_at * 1000).toLocaleDateString("ru-RU")}. Оформи подписку, чтобы продолжить.`;
    } else {
      title.textContent = "Подписка MOVENTA";
      status.textContent = "После 7 дней бесплатно оформи подписку, чтобы пользоваться MOVENTA.";
    }
    $("subscribe-button-label").textContent =
      access.status === "trial" ? "Оплатить сейчас" : "Оплатить подписку";
    $("subscription-screen").hidden = false;
  }

  function renderProfileSubscription(access) {
    const badge = $("profile-subscription-badge");
    const info = $("profile-subscription-info");
    const button = $("profile-subscription-button");
    const endDate = (timestamp) => new Date(timestamp * 1000).toLocaleDateString("ru-RU");
    button.hidden = !access;
    if (!access) {
      badge.textContent = "Недоступно";
      info.textContent = "Заполни профиль, чтобы начать пробный период.";
      return;
    }

    if (access.status === "trial") {
      badge.textContent = "Пробный доступ";
      badge.className = "subscription-badge trial";
      info.textContent = `Бесплатно до ${endDate(access.trial_expires_at)}. Можно оплатить подписку сразу — 30-дневный период начнётся после оплаты.`;
      $("profile-subscription-button-label").textContent = "Оплатить сейчас";
    } else if (access.status === "paid") {
      badge.textContent = "Активна";
      badge.className = "subscription-badge";
      info.textContent = `Доступ до ${endDate(access.paid_until)}. Подписка продлевается автоматически; отмена — в настройках Telegram → Мои звёзды → Подписки.`;
      button.hidden = true;
    } else {
      badge.textContent = "Не активна";
      badge.className = "subscription-badge expired";
      info.textContent = `Пробный период завершился ${endDate(access.trial_expires_at)}. Оформи подписку за ${access.price_stars || 300} ⭐ на 30 дней.`;
      $("profile-subscription-button-label").textContent = "Оформить подписку";
    }
  }

  function showError(error) {
    if (error.status === 402) return;
    if (error.status === 401) {
      showToast(hasTelegramAuth
        ? "Не удалось подтвердить вход. Закрой приложение и открой его заново — если не поможет, напиши в поддержку через /paysupport."
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
          : response.status === 409 && path.startsWith("/api/reports")
            ? "Ты недавно уже отправлял такое сообщение. Попробуй позже."
          : response.status === 429 && path.startsWith("/api/reports")
            ? "Лимит сообщений на сегодня достигнут. Попробуй позже."
          : response.status === 409 && path === "/api/privacy/delete"
            ? "Сначала отмени автопродление подписки в настройках Telegram."
          : response.status === 409
            ? "Подписка уже активна."
          : "Сервис временно недоступен. Попробуй ещё раз.",
      );
      error.status = response.status;
      if (response.status === 402) showSubscriptionScreen();
      throw error;
    }
    return response;
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
    const query = new URLSearchParams({ work_type: workType });
    const response = await api(`/api/districts?${query}`, { cache: "no-store" });
    const data = await response.json();
    renderDistricts(data.district_groups);
  }

  async function loadDriverReports() {
    const query = new URLSearchParams({ work_type: workType });
    const response = await api(`/api/reports?${query}`, { cache: "no-store" });
    const data = await response.json();
    renderDriverReports(data.reports || []);
  }

  async function loadCityWeather() {
    const data = await (await api("/api/weather", { cache: "no-store" })).json();
    // Если прогноз уже показал погоду из Open-Meteo, не перетираем её
    if (forecastWeatherApplied) return;
    const badge = $("city-weather");
    badge.textContent = data.ok
      ? `${data.text} · wttr.in`
      : `${data.text} · погода недоступна`;
    badge.classList.toggle("unavailable", !data.ok);
  }

  async function loadAdminPanel() {
    if (!hasTelegramAuth) return;
    const response = await fetch("/api/admin/summary", { headers });
    if (response.status === 401 || response.status === 403) {
      $("admin-panel").hidden = true;
      return;
    }
    if (!response.ok) throw new Error("Не удалось загрузить панель администратора.");
    const summary = await response.json();
    const reportsResponse = await fetch("/api/admin/reports", { headers });
    if (!reportsResponse.ok) {
      throw new Error("Не удалось загрузить сообщения для модерации.");
    }
    const reports = (await reportsResponse.json()).reports || [];
    renderAdminPanel(summary, reports);
  }

  function renderAdminPanel(summary, reports) {
    const metrics = [
      ["Пользователи", summary.total_users],
      ["Активны за 30 дней", summary.active_30d],
      ["Такси", summary.taxi_profiles],
      ["Доставка", summary.courier_profiles],
      ["Оба направления", summary.both_direction_users],
      ["Смены", summary.work_shifts],
      ["Активные сообщения", summary.active_reports],
      ["Подписки и trial", summary.active_subscriptions],
    ];
    const metricsContainer = $("admin-metrics");
    metricsContainer.replaceChildren();
    metrics.forEach(([label, value]) => {
      const card = document.createElement("div");
      card.className = "admin-metric";
      const title = document.createElement("span");
      title.textContent = label;
      const amount = document.createElement("strong");
      amount.textContent = String(value || 0);
      card.append(title, amount);
      metricsContainer.appendChild(card);
    });

    const quality = $("admin-forecast-quality");
    quality.replaceChildren();
    const metricLabels = {
      taxi: "Такси · средняя ошибка коэффициента",
      courier: "Доставка · средняя ошибка дохода/ч",
    };
    Object.entries(summary.forecast_quality || {}).forEach(([type, data]) => {
      const row = document.createElement("div");
      row.className = "admin-quality-row";
      const title = document.createElement("span");
      title.textContent = metricLabels[type] || type;
      const result = document.createElement("strong");
      const error = data.mean_absolute_error === null
        ? "пока нет проверенных прогнозов"
        : type === "taxi"
          ? `${data.mean_absolute_error.toFixed(2)}x`
          : `${currency.format(data.mean_absolute_error)}/ч`;
      result.textContent = `${error} · ${data.evaluated || 0} оценено`;
      row.append(title, result);
      quality.appendChild(row);
    });

    const coverage = summary.district_coverage || {};
    const worst = summary.forecast_quality_by_district || {};
    ["taxi", "courier"].forEach((type) => {
      const label = type === "taxi" ? "Такси" : "Доставка";
      const cover = coverage[type];
      if (cover) {
        const row = document.createElement("div");
        row.className = "admin-quality-row";
        const title = document.createElement("span");
        title.textContent = `${label} · покрытие районов`;
        const value = document.createElement("strong");
        value.textContent = `${cover.covered} из ${cover.total} (${cover.share}%)`;
        row.append(title, value);
        quality.appendChild(row);
      }
      const rows = worst[type] || [];
      if (!rows.length) return;
      const block = document.createElement("div");
      block.className = "admin-quality-districts";
      const heading = document.createElement("p");
      heading.className = "eyebrow admin-reports-label";
      heading.textContent = `${label.toUpperCase()} · ГДЕ ОШИБАЕМСЯ СИЛЬНЕЕ`;
      block.appendChild(heading);
      rows.slice(0, 5).forEach((item) => {
        const line = document.createElement("p");
        line.className = "admin-quality-line";
        const error = type === "taxi"
          ? `${item.mean_absolute_error}x`
          : `${currency.format(item.mean_absolute_error)}/ч`;
        line.textContent = `${item.district} — ${error} · ${item.evaluated} проверок`;
        block.appendChild(line);
      });
      quality.appendChild(block);
    });

    const reportsContainer = $("admin-reports");
    reportsContainer.replaceChildren();
    if (!reports.length) {
      const empty = document.createElement("p");
      empty.className = "empty-note";
      empty.textContent = "Нет актуальных сообщений для модерации.";
      reportsContainer.appendChild(empty);
    }
    reports.forEach((report) => {
      const card = document.createElement("article");
      card.className = "admin-report";
      const heading = document.createElement("strong");
      heading.textContent = `${report.work_type === "taxi" ? "Такси" : "Доставка"} · ${reportCategoryLabels[report.category] || report.category}`;
      const meta = document.createElement("p");
      meta.className = "admin-report-meta";
      meta.textContent = `${report.district} · Telegram ID ${report.user_id} · ${formatCoefficientTime(report.created_at)} · ${report.status}`;
      const details = document.createElement("p");
      details.className = "driver-report-details";
      details.textContent = report.details || "Без заметки";
      const action = document.createElement("button");
      action.className = "text-button";
      action.type = "button";
      action.dataset.moderateReport = "true";
      action.dataset.reportId = String(report.id);
      action.dataset.status = report.status === "active" ? "hidden" : "active";
      action.textContent = report.status === "active" ? "Скрыть сообщение" : "Восстановить";
      card.append(heading, meta, details, action);
      reportsContainer.appendChild(card);
    });
    $("admin-panel").hidden = false;
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
      if (hasTelegramAuth) {
        Promise.all([refreshMarketStats(), loadDriverReports()]).catch(showError);
      }
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

  function fitCoefficientMapToViewportIfResized() {
    const { width, height } = mapContainer.getBoundingClientRect();
    if (!width || !height) return;
    const size = `${Math.round(width)}x${Math.round(height)}`;
    if (size === coefficientMapViewportSize) return;
    coefficientMapViewportSize = size;
    fitCoefficientMapToViewport();
  }

  const mapContainer = $("coefficient-map");
  if (typeof ResizeObserver !== "undefined") {
    const mapResizeObserver = new ResizeObserver(fitCoefficientMapToViewportIfResized);
    mapResizeObserver.observe(mapContainer);
  }
  window.addEventListener("resize", fitCoefficientMapToViewportIfResized);

  function updateWorkTypeButtons() {
    document.querySelectorAll("[data-work-type]").forEach((button) => {
      button.hidden = !availableWorkTypes.includes(button.dataset.workType);
      button.classList.toggle("active", button.dataset.workType === workType);
    });
    const typeSelect = $("profile-work-type");
    const labels = { taxi: "Такси", courier: "Курьерская доставка" };
    typeSelect.replaceChildren(
      ...availableWorkTypes.map((type) => new Option(labels[type], type)),
    );
    typeSelect.value = workType;
    const directionsList = $("profile-directions-list");
    directionsList.replaceChildren();
    availableWorkTypes.forEach((type) => {
      const direction = document.createElement("span");
      direction.className = "profile-direction-chip";
      direction.textContent = labels[type];
      directionsList.appendChild(direction);
    });
    const addDirectionButton = $("add-direction-button");
    const missingType = availableWorkTypes.includes("taxi") ? "courier" : "taxi";
    addDirectionButton.hidden = availableWorkTypes.length >= 2;
    addDirectionButton.textContent = `Добавить ${labels[missingType].toLocaleLowerCase("ru")}`;
  }

  function setWorkType(value) {
    if (!availableWorkTypes.includes(value)) {
      showToast("Добавь это направление в профиле, чтобы начать.");
      return;
    }
    workType = value;
    updateWorkTypeButtons();
    if (hasTelegramAuth) {
      Promise.all([loadDashboard(), loadProfile(value)]).catch(showError);
    }
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

  function updateReportForm() {
    const currentDistrict = $("report-district").value;
    fillDistrictSelect($("report-district"), "Выбери район", currentDistrict);
    const categorySelect = $("report-category");
    const currentCategory = categorySelect.value;
    categorySelect.replaceChildren(
      new Option("Выбери событие", ""),
      ...reportCategories[workType].map((category) => new Option(
        reportCategoryLabels[category],
        category,
      )),
    );
    if (reportCategories[workType].includes(currentCategory)) {
      categorySelect.value = currentCategory;
    }
  }

  function renderDriverReports(reports) {
    const container = $("driver-reports");
    container.replaceChildren();
    if (!reports.length) {
      const empty = document.createElement("p");
      empty.className = "empty-note";
      empty.textContent = "Пока нет сообщений по выбранному виду работы. Поделись тем, что видишь.";
      container.appendChild(empty);
      return;
    }

    reports.forEach((report) => {
      const card = document.createElement("article");
      card.className = "driver-report";
      const heading = document.createElement("div");
      heading.className = "driver-report-heading";
      const title = document.createElement("strong");
      title.textContent = reportCategoryLabels[report.category] || "Обстановка";
      const confidence = document.createElement("span");
      confidence.className = `report-confidence ${report.confidence || "single"}`;
      confidence.textContent = report.confidence === "high"
        ? "3+ водителей"
        : report.confidence === "corroborated"
          ? "Подтвердили двое"
          : "Одно сообщение";
      heading.append(title, confidence);
      const note = document.createElement("p");
      note.className = "driver-report-details";
      note.textContent = report.details || "Без дополнительной заметки";
      const meta = document.createElement("span");
      meta.className = "driver-report-meta";
      meta.textContent = `${report.district} · ${formatCoefficientTime(report.created_at)} · до ${formatCoefficientTime(report.expires_at)}`;
      card.append(heading, note, meta);
      container.appendChild(card);
    });
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
          : district.report_count
            ? `${district.report_count} ${plural(district.report_count, "сообщение", "сообщения", "сообщений")}`
            : "пока без замеров";
        if (district.records || district.report_count) statistic.classList.add("has-data");
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
    updateReportForm();
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
      district.report_count,
      district.reporters,
      district.report_categories,
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
        const reportCount = Number(district.report_count) || 0;
        const reporterCount = Number(district.reporters) || 0;
        const color = coefficientColor(district.avg_coefficient, hasData);
        const sampleCount = Number(district.records) || 0;
        const reportedAt = formatCoefficientTime(district.last_update);
        const coefficientDescription = hasData
          ? `Средний коэффициент: <strong>${Number(district.avg_coefficient).toFixed(1)}x</strong><br>${sampleCount} ${plural(sampleCount, "замер", "замера", "замеров")}<br>Последний замер: ${reportedAt}`
          : "Пока нет замеров коэффициента от водителей";
        const communityDescription = reportCount
          ? `Сообщения сообщества: <strong>${reportCount}</strong> · ${reporterCount} ${plural(reporterCount, "водитель", "водителя", "водителей")}`
          : "";
        const balloonBody = communityDescription
          ? `${coefficientDescription}<br><br>${communityDescription}`
          : coefficientDescription;
        coefficientAreas.add(new ymaps.GeoObject({
          geometry: {
            type: boundary.type,
            coordinates: toYandexCoordinates(boundary),
          },
          properties: {
            balloonContentHeader: district.name,
            balloonContentBody: balloonBody,
            hintContent: hasData
              ? `${district.name} · ${Number(district.avg_coefficient).toFixed(1)}x${reportCount ? ` · ${reportCount} сообщ.` : ""} · ${reportedAt}`
              : `${district.name} · ${reportCount ? `${reportCount} сообщ. сообщества` : "нет замеров"}`,
          },
        }, {
          fillColor: color,
          fillOpacity: hasData ? 0.45 : 0.06,
          strokeColor: hasData ? color : reportCount ? "#ffbf55" : "#87939e",
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

  let selectedHorizon = 60;
  let currentForecast = null;
  let forecastWeatherApplied = false;

  function scoreFor(item, horizon) {
    const value = item.horizons ? item.horizons[String(horizon)] : undefined;
    return Number(value === undefined || value === null ? item.score || 0 : value);
  }

  function updateWeatherBadge(weather) {
    if (!weather || typeof weather.temperature !== "number") return;
    const badge = $("city-weather");
    if (!badge) return;
    forecastWeatherApplied = true;
    const parts = [weather.emoji, weather.text, `${Math.round(weather.temperature)} °C`]
      .filter(Boolean)
      .join(" ");
    badge.textContent = `${parts} · Open-Meteo`;
    badge.classList.remove("unavailable");
  }

  function renderRecommendations(forecast) {
    const container = $("recommendations");
    const items = forecast.items || [];
    currentForecast = forecast;
    updateWeatherBadge(forecast.weather);
    const hasHorizons = items.some((item) => item.horizons);
    $("horizon-switch").hidden = !hasHorizons;
    $("forecast-chip").hidden = forecast.method !== "poi_baseline";
    $("forecast-title").textContent = forecast.title || "Перспективные районы";
    $("forecast-eyebrow").textContent = forecast.work_type === "taxi"
      ? "СИГНАЛ НА БЛИЖАЙШИЙ ЧАС"
      : "ЛИЧНАЯ АНАЛИТИКА";
    $("forecast-note").textContent = forecast.note || "";
    container.replaceChildren();
    (forecast.hints || []).forEach((text) => {
      const hint = document.createElement("p");
      hint.className = "forecast-hint";
      hint.textContent = text;
      container.appendChild(hint);
    });
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "empty-note";
      empty.textContent = forecast.empty_message || "Пока недостаточно данных для оценки.";
      container.appendChild(empty);
      return;
    }

    const ranked = [...items].sort(
      (a, b) => scoreFor(b, selectedHorizon) - scoreFor(a, selectedHorizon)
    );
    ranked.forEach((item, index) => {
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
      const trendLabels = forecast.work_type === "taxi" ? {
        rising: "коэффициент растёт",
        falling: "коэффициент снижается",
        steady: "коэффициент стабилен",
        unknown: "динамика неизвестна",
      } : {
        rising: "доход/ч выше",
        falling: "доход/ч ниже",
        steady: "доход/ч стабилен",
        unknown: "недостаточно истории для динамики",
      };
      if (item.method === "poi_baseline") {
        note.textContent = `стартовая оценка по заведениям · уверенность ${item.confidence}%`;
      } else {
        note.textContent = `${item.sample_count} наблюдений · уверенность ${item.confidence}% · ${trendLabels[item.trend] || trendLabels.unknown}`;
      }
      const metric = document.createElement("span");
      metric.className = "forecast-metric";
      if (item.current_coefficient !== undefined) {
        metric.textContent = item.current_coefficient === null
          ? `История ${item.historical_coefficient}x`
          : `Сейчас ${item.current_coefficient}x`;
      } else if (item.metric_label === "плотность заведений") {
        metric.textContent = "плотность кафе и магазинов";
      } else {
        const periodLabel = item.recent_samples ? "Последние 45 дней" : "За 90 дней";
        metric.textContent = `${periodLabel}: ${currency.format(item.net_income_per_hour || 0)}/ч`;
      }
      const track = document.createElement("div");
      track.className = "forecast-score-track";
      const displayScore = scoreFor(item, selectedHorizon);
      const fill = document.createElement("span");
      fill.style.width = `${displayScore}%`;
      track.appendChild(fill);
      info.append(name, note, metric);
      if (item.score_base !== undefined && Number(item.score_base) !== Number(item.score)) {
        const delta = document.createElement("span");
        delta.className = "score-delta";
        delta.textContent = `база ${item.score_base} → ${item.score} с погодой`;
        info.append(delta);
      }
      info.append(track);
      const score = document.createElement("strong");
      score.className = "recommendation-coef";
      score.textContent = `${displayScore}/100`;
      card.append(rank, info, score);
      container.appendChild(card);
    });
    container.appendChild(renderMethod(forecast));
  }

  function renderMethod(forecast) {
    const factorLabels = {
      weather: "погода",
      poi_density: "плотность заведений",
      airports: "аэропорты",
      events: "мероприятия",
      reports: "сообщения водителей",
    };
    const factors = forecast.factors || {};
    const used = Object.entries(factorLabels)
      .filter(([key]) => factors[key])
      .map(([, label]) => label);

    const block = document.createElement("details");
    block.className = "forecast-method";
    const summary = document.createElement("summary");
    summary.textContent = "Как считается прогноз";
    const first = document.createElement("p");
    first.textContent = used.length
      ? `Сейчас участвуют: ${used.join(", ")}.`
      : "Внешние данные сейчас недоступны, оценка построена по вашей истории.";
    const second = document.createElement("p");
    second.textContent = "Балл 0–100 сравнивает районы между собой, это не число заказов. " +
      "60 минут — основная оценка, 30 и 120 показывают ожидаемый сдвиг по типичному профилю суток.";
    block.append(summary, first, second);
    return block;
  }

  $("horizon-switch").addEventListener("click", (event) => {
    const button = event.target.closest("[data-horizon]");
    if (!button) return;
    selectedHorizon = Number(button.dataset.horizon);
    document.querySelectorAll("#horizon-switch .horizon-button").forEach((el) => {
      el.classList.toggle("active", el === button);
    });
    if (currentForecast) renderRecommendations(currentForecast);
  });

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

  function renderWeekdayEfficiency(items) {
    const container = $("weekday-efficiency");
    container.replaceChildren();
    const orderedDays = [1, 2, 3, 4, 5, 6, 0];
    const labels = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
    const byDay = new Map(items.map((item) => [item.weekday, item]));
    orderedDays.forEach((day) => {
      const item = byDay.get(day);
      if (!item) return;
      const row = document.createElement("div");
      row.className = "weekday-row";
      const label = document.createElement("span");
      label.textContent = labels[day];
      const detail = document.createElement("span");
      detail.textContent = `${currency.format(item.net_income_per_hour)}/ч · ${item.hours_worked} ч`;
      row.append(label, detail);
      container.appendChild(row);
    });
    if (!container.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "empty-note";
      empty.textContent = "Добавляй смены, чтобы сравнить эффективность по дням.";
      container.appendChild(empty);
    }
  }

  function renderExpenseBreakdown(expenses, total) {
    const container = $("expense-breakdown");
    container.replaceChildren();
    const labels = [
      ["fuel_cost", "Топливо"],
      ["commission_cost", "Комиссии"],
      ["maintenance_cost", "Обслуживание"],
      ["rental_cost", "Аренда"],
      ["insurance_cost", "Страхование"],
      ["tax_cost", "Налоги"],
      ["depreciation_cost", "Амортизация"],
      ["charging_cost", "Зарядка"],
      ["connectivity_cost", "Связь"],
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
        + Number(shift.maintenance_cost) + Number(shift.other_cost)
        + Number(shift.rental_cost || 0) + Number(shift.insurance_cost || 0)
        + Number(shift.tax_cost || 0) + Number(shift.charging_cost || 0)
        + Number(shift.connectivity_cost || 0);
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
    + read("maintenance_cost") + read("other_cost") + read("rental_cost")
    + read("insurance_cost") + read("tax_cost") + read("charging_cost")
    + read("connectivity_cost");
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

  async function loadProfile(selectedWorkType = null) {
    const query = selectedWorkType
      ? `?${new URLSearchParams({ work_type: selectedWorkType })}`
      : "";
    const profile = await (await api(`/api/profile${query}`)).json();
    currentSubscription = profile.subscription;
    renderProfileSubscription(currentSubscription);
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
    const notificationForm = $("notification-form");
    const notifications = profile.notifications || {};
    [
      "demand_changes", "promising_areas", "weather_changes", "city_events",
      "traffic_events", "personal_recommendations", "financial_reminders",
    ].forEach((field) => {
      const input = notificationForm.elements[field];
      // Отключённые пункты («скоро») всегда сохраняются как выключенные
      input.checked = input.disabled ? false : Boolean(notifications[field]);
    });
    notificationForm.elements.frequency.value = notifications.frequency || "daily";
    notificationForm.elements.quiet_start.value = notifications.quiet_start || "";
    notificationForm.elements.quiet_end.value = notifications.quiet_end || "";
    workType = profile.work_type;
    availableWorkTypes = profile.directions || [];
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

  async function refreshAfterPayment() {
    for (let attempt = 0; attempt < 6; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      const profile = await loadProfile(workType);
      if (profile.subscription && profile.subscription.active) {
        $("subscription-screen").hidden = true;
        await loadDashboard();
        return;
      }
    }
    $("subscription-message").textContent =
      "Платёж принят. Доступ обновится в ближайшее время — нажми «Обновить».";
  }

  $("subscription-consent").addEventListener("change", (event) => {
    $("subscribe-button").disabled = !event.currentTarget.checked;
  });
  document.querySelector(".subscription-consent a").addEventListener("click", () => {
    $("subscription-terms").open = true;
  });
  $("premium-bot-link").addEventListener("click", (event) => {
    if (tg && tg.openTelegramLink) {
      event.preventDefault();
      tg.openTelegramLink(event.currentTarget.href);
    }
  });
  async function startSubscriptionPayment(button) {
    button.disabled = true;
    $("subscription-message").textContent = "Готовим счёт Telegram Stars…";
    try {
      const result = await (await api("/api/subscription/invoice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accept_terms: $("subscription-consent").checked }),
      })).json();
      $("subscription-message").textContent = "";
      if (tg && typeof tg.openInvoice === "function") {
        tg.openInvoice(result.invoice_url, (status) => {
          if (status === "paid") {
            refreshAfterPayment().catch(showError);
          } else if (status === "failed") {
            $("subscription-message").textContent = "Не удалось выполнить платёж. Попробуй ещё раз.";
          } else if (status === "cancelled") {
            $("subscription-message").textContent = "Оплата отменена.";
          }
          button.disabled = !$("subscription-consent").checked;
        });
      } else if (tg && tg.openTelegramLink) {
        tg.openTelegramLink(result.invoice_url);
      } else if (tg && tg.openLink) {
        tg.openLink(result.invoice_url);
      } else {
        window.location.assign(result.invoice_url);
      }
    } catch (error) {
      showError(error);
      $("subscription-message").textContent = error.message || "Не удалось создать счёт.";
      button.disabled = !$("subscription-consent").checked;
    }
  }

  $("subscribe-button").addEventListener("click", (event) => {
    startSubscriptionPayment(event.currentTarget);
  });
  $("profile-subscription-button").addEventListener("click", () => {
    showSubscriptionScreen(currentSubscription);
  });

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
    $("economic-profit").textContent = currency.format(data.summary.economic_profit);
    $("expenses-per-km").textContent = `${currency.format(data.summary.expenses_per_km)}/км`;
    $("average-check").textContent = currency.format(data.summary.average_check);
    $("average-order-duration").textContent = data.summary.orders_count
      ? `${data.summary.average_order_duration_minutes} мин`
      : "—";
    const comparison = $("period-comparison");
    if (data.summary.previous_period_shifts) {
      const difference = data.summary.net_income
        - data.summary.previous_period_net_income;
      comparison.textContent = `${difference >= 0 ? "+" : "−"}${currency.format(Math.abs(difference))} к предыдущему периоду`;
      comparison.classList.toggle("positive", difference >= 0);
      comparison.classList.toggle("negative", difference < 0);
    } else {
      comparison.textContent = "Пока нет данных за предыдущий период для сравнения";
      comparison.classList.remove("positive", "negative");
    }
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
    renderWeekdayEfficiency(data.summary.weekday_efficiency || []);
    renderExpenseBreakdown(data.summary.expenses, data.summary.economic_expenses);
    renderRecommendations(data.forecast);
    renderShifts($("recent-shifts"), data.summary.recent_shifts, "За этот период смен ещё нет. Добавь первую в разделе «Смена».");
    renderShifts($("shift-history"), data.summary.recent_shifts, "За выбранный период смен ещё нет.");
    renderDistricts(data.district_groups);
    await loadDriverReports();
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
  $("add-direction-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const direction = availableWorkTypes.includes("taxi") ? "courier" : "taxi";
    button.disabled = true;
    try {
      await api("/api/profile/directions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ work_type: direction }),
      });
      workType = direction;
      await loadProfile(direction);
      await loadDashboard();
      showToast("Направление добавлено.");
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
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
  $("refresh-reports").addEventListener("click", () => {
    loadDriverReports().catch(showError);
  });
  $("admin-refresh").addEventListener("click", () => {
    loadAdminPanel().catch((error) => {
      $("admin-message").textContent = error.message || "Не удалось обновить данные.";
    });
  });
  $("admin-reports").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-moderate-report]");
    if (!button) return;
    button.disabled = true;
    $("admin-message").textContent = "";
    try {
      const response = await fetch(`/api/admin/reports/${button.dataset.reportId}`, {
        method: "PATCH",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ status: button.dataset.status }),
      });

      $("export-data").addEventListener("click", async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        $("privacy-message").textContent = "";
        try {
          const response = await api("/api/privacy/export");
          const downloadUrl = URL.createObjectURL(await response.blob());
          const link = document.createElement("a");
          link.href = downloadUrl;
          link.download = "moventa-data-export.json";
          link.click();
          window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
          $("privacy-message").textContent = "Копия данных подготовлена.";
        } catch (error) {
          $("privacy-message").textContent = error.message || "Не удалось скачать данные.";
          showError(error);
        } finally {
          button.disabled = false;
        }
      });

      $("delete-data").addEventListener("click", async (event) => {
        if (!window.confirm(
          "Удалить профиль и все данные Moventa? Это действие нельзя отменить.",
        )) return;
        const button = event.currentTarget;
        button.disabled = true;
        $("privacy-message").textContent = "";
        try {
          await api("/api/privacy/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm: true }),
          });
          document.querySelectorAll(".onboarding-screen, .subscription-screen")
            .forEach((screen) => { screen.hidden = true; });
          const shell = document.querySelector(".app-shell");
          shell.replaceChildren();
          const message = document.createElement("section");
          message.className = "privacy-card";
          const title = document.createElement("h1");
          title.textContent = "Данные удалены";
          const detail = document.createElement("p");
          detail.textContent = "Данные приложения удалены. При повторном запуске можно будет создать новый профиль.";
          message.append(title, detail);
          shell.appendChild(message);
        } catch (error) {
          $("privacy-message").textContent = error.message || "Не удалось удалить данные.";
          showError(error);
          button.disabled = false;
        }
      });
      if (!response.ok) throw new Error("Не удалось изменить статус сообщения.");
      await loadAdminPanel();
    } catch (error) {
      $("admin-message").textContent = error.message || "Не удалось выполнить модерацию.";
      button.disabled = false;
    }
  });

  $("report-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    $("report-message").textContent = "";
    try {
      await api("/api/reports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          work_type: workType,
          category: form.elements.category.value,
          district: form.elements.district.value,
          details: form.elements.details.value.trim(),
        }),
      });
      form.elements.details.value = "";
      $("report-message").textContent = "Спасибо! Сообщение появится на карте.";
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      await Promise.all([loadDriverReports(), refreshMarketStats()]);
    } catch (error) {
      $("report-message").textContent = error.message || "Не удалось отправить сообщение.";
      showError(error);
    } finally {
      button.disabled = false;
    }
  });

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
      await loadProfile(workType);
      await loadDashboard();
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });

  $("notification-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    $("notification-message").textContent = "";
    const fields = [
      "demand_changes", "promising_areas", "weather_changes", "city_events",
      "traffic_events", "personal_recommendations", "financial_reminders",
    ];
    const payload = Object.fromEntries(
      fields.map((field) => [field, form.elements[field].checked]),
    );
    payload.frequency = form.elements.frequency.value;
    payload.quiet_start = form.elements.quiet_start.value;
    payload.quiet_end = form.elements.quiet_end.value;
    try {
      await api("/api/notifications/preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      $("notification-message").textContent = "Настройки сохранены.";
    } catch (error) {
      $("notification-message").textContent = error.message || "Не удалось сохранить настройки.";
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
      if (!profile.subscription || !profile.subscription.active) {
        $("onboarding-screen").hidden = true;
        showSubscriptionScreen(profile.subscription);
        return;
      }
      await loadDashboard();
      $("onboarding-screen").hidden = true;
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      loadAdminPanel().catch(showError);
      loadCityWeather().catch(showError);
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
          rental_cost: value("rental_cost"),
          insurance_cost: value("insurance_cost"),
          tax_cost: value("tax_cost"),
          depreciation_cost: value("depreciation_cost"),
          charging_cost: value("charging_cost"),
          connectivity_cost: value("connectivity_cost"),
          hours_worked: value("hours_worked"),
          distance_km: value("distance_km"),
          orders_count: Number(form.elements.orders_count.value || 0),
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
          $("onboarding-intro").textContent = "Заполни профиль один раз, чтобы открыть MOVENTA.";
          $("onboarding-message").textContent = "";
          $("onboarding-name").focus();
          return;
        }
        if (!profile.subscription || !profile.subscription.active) {
          onboardingScreen.hidden = true;
          showSubscriptionScreen(profile.subscription);
          return;
        }
        await loadDashboard();
        onboardingScreen.hidden = true;
        loadAdminPanel().catch(showError);
        loadCityWeather().catch(showError);
        loadMapResources().catch(showError);
      } catch (error) {
        if (error.status === 402) {
          onboardingScreen.hidden = true;
          return;
        }
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
  }
  const refreshVisibleMarketStats = () => {
    if (hasTelegramAuth && !document.hidden) {
      Promise.all([refreshMarketStats(), loadDriverReports()]).catch(showError);
    }
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
