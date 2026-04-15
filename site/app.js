const state = {
  filter: "all",
  payload: null,
};

const filterRow = document.getElementById("filter-row");
const heroMeta = document.getElementById("hero-meta");
const metricGrid = document.getElementById("metric-grid");
const entries = document.getElementById("entries");
const emptyState = document.getElementById("empty-state");

function importanceLabel(level) {
  if (level === "high") return "High";
  if (level === "medium") return "Medium";
  return "Low";
}

function formatPublished(value) {
  if (!value) return "公開日時不明";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Tokyo",
  }).format(date);
}

function renderHeroMeta(payload) {
  heroMeta.innerHTML = "";
  const fetchedCount = payload.max_items ?? payload.item_count;
  const items = [
    `更新: ${payload.generated_at_label}`,
    `記事数: ${payload.item_count}件`,
    `取得: latest ${fetchedCount}`,
    "分類: keyword-only",
  ];

  items.forEach((label) => {
    const chip = document.createElement("span");
    chip.className = "meta-chip";
    chip.textContent = label;
    heroMeta.append(chip);
  });
}

function renderMetrics(payload) {
  metricGrid.innerHTML = "";

  const cards = [
    { title: "High", value: payload.counts.high, note: "運用・管理影響が強め" },
    { title: "Medium", value: payload.counts.medium, note: "公開範囲や SDK 更新" },
    { title: "Low", value: payload.counts.low, note: "改善・性能向上中心" },
    { title: "Latest", value: payload.latest_published ? formatPublished(payload.latest_published) : "-", note: "直近の公開日時" },
  ];

  cards.forEach((card) => {
    const element = document.createElement("article");
    const title = document.createElement("span");
    const value = document.createElement("strong");
    const note = document.createElement("span");

    element.className = "metric-card";
    title.textContent = card.title;
    value.textContent = String(card.value);
    note.textContent = card.note;

    element.append(title, value, note);
    metricGrid.append(element);
  });
}

function createKeywordChip(keyword) {
  const chip = document.createElement("span");
  chip.className = "keyword-chip";
  chip.textContent = keyword;
  return chip;
}

function renderEntries() {
  const payload = state.payload;
  if (!payload) return;

  const filteredItems = payload.items.filter((item) => {
    if (state.filter === "all") return true;
    return item.importance === state.filter;
  });

  entries.innerHTML = "";
  emptyState.hidden = filteredItems.length !== 0;

  filteredItems.forEach((item) => {
    const tags = item.tags ?? [];
    const matchedKeywords = item.matched_keywords ?? [];
    const card = document.createElement("article");
    card.className = "entry-card";

    const head = document.createElement("div");
    head.className = "entry-head";

    const title = document.createElement("h3");
    title.className = "entry-title";

    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = item.title;
    title.append(link);

    const importanceChip = document.createElement("span");
    importanceChip.className = "importance-chip";
    importanceChip.dataset.level = item.importance;
    importanceChip.textContent = importanceLabel(item.importance);

    head.append(title, importanceChip);

    const meta = document.createElement("p");
    meta.className = "entry-meta";
    meta.textContent = formatPublished(item.published_iso || item.published);

    const summary = document.createElement("p");
    summary.className = "entry-summary";
    summary.textContent = item.summary || "RSS 要約はありません。";

    const reason = document.createElement("p");
    reason.className = "entry-reason";
    reason.textContent = `判定理由: ${item.reason_ja}`;

    card.append(head, meta, summary, reason);

    const chipRow = document.createElement("div");
    chipRow.className = "chip-row";

    if (item.changelog_type) {
      chipRow.append(createKeywordChip(item.changelog_type));
    }

    tags.forEach((tag) => {
      chipRow.append(createKeywordChip(tag));
    });

    if (matchedKeywords.length) {
      matchedKeywords.forEach((keyword) => {
        chipRow.append(createKeywordChip(keyword));
      });
    }

    if (chipRow.children.length) {
      card.append(chipRow);
    }

    entries.append(card);
  });
}

function syncFilterButtons() {
  const buttons = filterRow.querySelectorAll(".filter-button");
  buttons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.filter === state.filter);
  });
}

async function loadData() {
  const response = await fetch("./data.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`data.json の取得に失敗しました: ${response.status}`);
  }

  state.payload = await response.json();
  renderHeroMeta(state.payload);
  renderMetrics(state.payload);
  renderEntries();
}

filterRow.addEventListener("click", (event) => {
  const button = event.target.closest(".filter-button");
  if (!button) return;

  state.filter = button.dataset.filter;
  syncFilterButtons();
  renderEntries();
});

loadData().catch((error) => {
  console.error(error);
  heroMeta.innerHTML = '<span class="meta-chip">データの読み込みに失敗しました</span>';
  emptyState.hidden = false;
  emptyState.textContent = "data.json を読み込めませんでした。";
});
