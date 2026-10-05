const api = async (path, options = {}) => {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401 && !location.pathname.startsWith("/login")) {
    location.href = "/login";
    throw new Error("请先登录");
  }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = data?.error?.message || data?.detail || String(data);
    throw new Error(message);
  }
  return data;
};

const showToast = (message, isError = false) => {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.style.background = isError ? "#cf2f42" : "#101828";
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 3500);
};

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

const statusLabels = {
  queued: "排队中", running: "处理中", succeeded: "成功", failed: "失败",
  review_pending: "待人工复核", canceled: "已取消", retrying: "重试中",
  dead: "死信", canceling: "取消中", pending: "待领取", in_review: "审核中",
  approved: "已通过", rejected: "已驳回"
};
const statusBadge = (status) => `<span class="badge ${escapeHtml(status)}">${escapeHtml(statusLabels[status] || status)}</span>`;
const formatTime = (value) => value ? new Date(value).toLocaleString("zh-CN") : "-";

const loadCurrentUser = async () => {
  const target = document.getElementById("current-user");
  try {
    const user = await api("/api/v1/auth/me");
    if (target) target.textContent = `${user.display_name} · ${user.role}`;
    return user;
  } catch {
    if (!location.pathname.startsWith("/login")) location.href = "/login";
    return null;
  }
};

document.addEventListener("DOMContentLoaded", async () => {
  const logout = document.getElementById("logout-button");
  if (logout) logout.addEventListener("click", async () => {
    await api("/api/v1/auth/logout", { method: "POST" });
    location.href = "/login";
  });
  if (!location.pathname.startsWith("/login")) await loadCurrentUser();
});
