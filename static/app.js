(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (!tg || !tg.initData) {
    document.getElementById("recommendations").innerHTML =
      '<div class="loading">Открой Mini App через Telegram, чтобы загрузить данные.</div>';
    return;
  }

  tg.ready();
  tg.expand();
  if (tg.setHeaderColor) tg.setHeaderColor("#10151c");
  if (tg.setBackgroundColor) tg.setBackgroundColor("#10151c");
  const headers = { "X-Telegram-Init-Data": tg.initData };
  const $ = (id) => document.getElementById(id);

  function showToast(message) {
    const toast = $("toast");
    toast.textContent = message;
    toast.classList.add("visible");
    window.setTimeout(() => toast.classList.remove("visible"), 3000);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...headers, ...(options.headers || {}) },
    });
    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `Ошибка ${response.status}`);
    }
    return response;
  }

  function renderRecommendations(items) {
    const container = $("recommendations");
    if (!items.length) {
      container.innerHTML = '<div class="loading">Пока мало данных для прогноза. Добавь первый коэффициент!</div>';
      return;
    }
    container.innerHTML = items.map((item, index) => `
      <article class="recommendation">
        <span class="rank">${index + 1}</span>
        <div><p class="district-name"></p><span class="district-detail"></span></div>
        <span class="coef">${item.avg_coefficient.toFixed(2)}x</span>
      </article>`).join("");
    container.querySelectorAll(".recommendation").forEach((card, index) => {
      const item = items[index];
      card.querySelector(".district-name").textContent = item.district;
      card.querySelector(".district-detail").textContent =
        `${item.data_points} замеров · надёжность ${item.reliability}%`;
    });
  }

  async function loadDashboard() {
    const data = await (await api("/api/dashboard")).json();
    $("driver-name").textContent = data.user.first_name || "водитель";
    $("total-records").textContent = data.stats.total_records;
    $("avg-coefficient").textContent = data.stats.avg_coefficient.toFixed(2);
    renderRecommendations(data.recommendations);
    const districtSelect = $("district");
    const selected = districtSelect.value;
    districtSelect.innerHTML = '<option value="">Выбери район</option>';
    data.districts.forEach((district) => {
      const option = document.createElement("option");
      option.value = district;
      option.textContent = district;
      districtSelect.appendChild(option);
    });
    districtSelect.value = selected;
    $("demand-map").src = `/api/map?ts=${Date.now()}`;
    $("demand-map").onload = () => {
      $("demand-map").hidden = false;
      $("map-loading").hidden = true;
    };
    $("demand-map").onerror = () => {
      $("map-loading").textContent = "Не удалось загрузить карту. Попробуй обновить.";
    };
  }

  $("refresh").addEventListener("click", () => loadDashboard().catch((error) => showToast(error.message)));
  $("coefficient-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    button.disabled = true;
    $("form-message").textContent = "";
    try {
      await api("/api/coefficients", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          district: $("district").value,
          coefficient: Number($("coefficient").value),
          weather: $("weather").value,
        }),
      });
      $("coefficient").value = "";
      $("form-message").textContent = "Коэффициент сохранён. Спасибо!";
      if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      await loadDashboard();
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  });

  loadDashboard().catch((error) => {
    $("recommendations").innerHTML = '<div class="loading">Не удалось загрузить данные.</div>';
    showToast(error.message);
  });
})();
