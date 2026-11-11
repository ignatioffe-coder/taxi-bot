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

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor("#11151b");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#11151b");
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
        ? "Сессия Telegram устарела. Закрой и открой приложение снова."
        : "Открой Mini App кнопкой в Telegram, чтобы загрузить данные.");
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
          : "Сервис временно недоступен. Попробуй ещё раз.",
      );
      error.status = response.status;
      throw error;
    }
    return response;
  }

  function setView(view) {
    document.querySelectorAll(".view").forEach((section) => {
      section.classList.toggle("active", section.id === `view-${view}`);
    });
    document.querySelectorAll(".nav-button").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === view);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setWorkType(value) {
    workType = value;
    document.querySelectorAll("[data-work-type]").forEach((button) => {
      button.classList.toggle("active", button.dataset.workType === value);
    });
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
          ? `${district.avg_coefficient.toFixed(1)}x · ${district.records} зам.`
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

  function updateShiftPreview() {
    const read = (name) => Number($(`shift-form`).elements[name].value || 0);
    const income = read("gross_income") + read("tips");
    const expenses = read("fuel_cost") + read("commission_cost")
      + read("maintenance_cost") + read("other_cost");
    const net = income - expenses;
    $("shift-net-preview").textContent = currency.format(net);
    $("shift-net-preview").style.color = net >= 0 ? "var(--mint)" : "var(--red)";
  }

  async function loadDashboard() {
    const query = new URLSearchParams({ work_type: workType, period });
    const data = await (await api(`/api/dashboard?${query}`)).json();
    const name = data.user.first_name || "водитель";
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
    loadDashboard().catch(showError);
  } else {
    const notice = '<p class="empty-note">Открой Mini App кнопкой в Telegram, чтобы загрузить данные.</p>';
    $("recommendations").innerHTML = notice;
    $("district-groups").innerHTML = notice;
    $("week-chart").innerHTML = notice;
    $("expense-breakdown").innerHTML = notice;
    $("recent-shifts").innerHTML = notice;
    $("shift-history").innerHTML = notice;
  }
})();
