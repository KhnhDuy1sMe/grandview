const token = sessionStorage.getItem("hotel_access_token");
const toastNode = document.querySelector("#managementToast");
let toastTimer;
let currentUser = null;
let activeView = "overview";
let serviceTimer = null;
let serviceFilter = "open";
let historyPage = 1;
const HISTORY_PAGE_SIZE = 20;
let historyRows = [];
let drawerRoomId = null;
let lastFocus = null;

const roleNames = { admin: "Quản trị viên", receptionist: "Lễ tân", customer: "Khách hàng" };
const roomStatusNames = { available: "Trống", occupied: "Đang có khách", cleaning: "Chờ dọn", maintenance: "Bảo trì" };
const statusNames = {
  ...roomStatusNames,
  pending: "Chờ xác nhận",
  confirmed: "Đã xác nhận",
  rejected: "Đã từ chối",
  cancelled: "Đã hủy",
  checked_in: "Đang lưu trú",
  checked_out: "Đã trả phòng",
};
const serviceStatusNames = { pending: "Chờ tiếp nhận", in_progress: "Đang xử lý", completed: "Đã hoàn tất", cancelled: "Đã hủy" };

// Thao tác được phép theo trạng thái đơn (khớp allowed_next ở backend)
// Hủy đơn, sửa đơn và đặt cọc nằm trong ngăn Chi tiết đơn.
const bookingActions = {
  pending: [["confirmed", "Xác nhận", "approve"], ["checked_in", "Nhận khách", ""], ["rejected", "Từ chối", "reject"]],
  confirmed: [["checked_in", "Nhận khách", "approve"]],
  checked_in: [["checked_out", "Trả phòng", "approve"]],
};
const depositMethodNames = { cash: "Tiền mặt", transfer: "Chuyển khoản", card: "Thẻ" };
const depositStatusNames = { paid: "Đã nhận cọc", applied: "Đã trừ vào hóa đơn", refunded: "Đã hoàn cọc", forfeited: "Giữ cọc (khách hủy)" };
let manageBookingId = null;
let manageBooking = null;
const bookingActionVerbs = {
  confirmed: "xác nhận", rejected: "từ chối", checked_in: "nhận khách cho", checked_out: "trả phòng cho", cancelled: "hủy",
};
const serviceActions = {
  pending: [["in_progress", "Tiếp nhận", "approve"], ["cancelled", "Hủy", "reject"]],
  in_progress: [["completed", "Hoàn tất", "approve"], ["cancelled", "Hủy", "reject"]],
};

function notify(message) {
  toastNode.textContent = message;
  toastNode.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastNode.classList.remove("show"), 4200);
}

async function apiRequest(url, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: `Bearer ${token}` };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) {
      sessionStorage.removeItem("hotel_access_token");
      window.location.replace("/");
    }
    const detail = Array.isArray(data.detail)
      ? data.detail.map(item => item.msg || "Dữ liệu không hợp lệ").join("; ")
      : (data.detail || `Yêu cầu thất bại (${response.status}).`);
    throw new Error(detail);
  }
  return data;
}

function formatMoney(value) {
  return new Intl.NumberFormat("vi-VN", {
    style: "currency", currency: "VND", maximumFractionDigits: 0,
  }).format(Number(value || 0));
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  return new Intl.DateTimeFormat("vi-VN").format(date);
}

function formatDateTime(value) {
  if (!value) return "—";
  return `${formatDate(value)} ${String(value).slice(11, 16)}`;
}

function nightsBetween(checkIn, checkOut) {
  return Math.round((new Date(checkOut) - new Date(checkIn)) / 86400000);
}

function escapeHtml(value = "") {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function codeCell(code) {
  const full = String(code || "");
  const short = full.length > 14 ? `${full.slice(0, 13)}…` : full;
  return `<span class="booking-code" title="${escapeHtml(full)}">${escapeHtml(short)}</span>`;
}

function badge(status, names = statusNames) {
  return `<span class="status-badge ${escapeHtml(status)}">${escapeHtml(names[status] || status)}</span>`;
}

function setCount(selector, value) {
  const node = document.querySelector(selector);
  node.textContent = value;
  node.dataset.count = String(value);
}

function todayISO() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

// Nút chuyển trạng thái; "Nhận khách" chỉ bật từ ngày nhận phòng trở đi.
function bookingActionButtons(booking, { withDetail = true } = {}) {
  const today = todayISO();
  const checkIn = String(booking.check_in).slice(0, 10);
  const checkOut = String(booking.check_out).slice(0, 10);
  const buttons = (bookingActions[booking.status] || []).map(([status, label, cls]) => {
    if (status === "checked_in") {
      if (today >= checkOut) return "";
      if (today < checkIn) {
        return `<button class="${cls} is-waiting" type="button" disabled
                  title="Chỉ nhận khách từ ngày ${formatDate(checkIn)}. Khách đến sớm thì sửa ngày nhận phòng trước.">Nhận khách từ ${formatDate(checkIn).replace(/\/\d{4}$/, "")}</button>`;
      }
    }
    return `<button class="${cls}" type="button" data-booking-action="${status}" data-booking-id="${Number(booking.id)}">${label}</button>`;
  });
  if (withDetail && ["pending", "confirmed", "checked_in", "checked_out", "cancelled", "rejected"].includes(booking.status)) {
    buttons.push(`<button type="button" data-open-booking="${Number(booking.id)}">Chi tiết</button>`);
  }
  return buttons.join("");
}

// ================= Điều hướng =================
function setView(view) {
  const allowed = currentUser?.role === "admin"
    ? ["overview", "rooms", "bookings", "services", "history"]
    : ["rooms", "bookings", "services", "history"];
  if (!allowed.includes(view)) view = allowed[0];
  activeView = view;
  document.querySelectorAll(".view-panel").forEach(panel => { panel.hidden = true; });
  document.querySelector(`#${view}View`).hidden = false;
  document.querySelectorAll(".nav-item").forEach(button => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  const titles = {
    overview: "Tổng quan vận hành", rooms: "Quản lý phòng", bookings: "Đơn đặt phòng",
    services: "Dịch vụ phòng", history: "Lịch sử đơn",
  };
  document.querySelector("#pageTitle").textContent = titles[view];
  document.querySelector("#sidebar").classList.remove("open");
  document.querySelector("#sidebarScrim").classList.remove("show");

  clearInterval(serviceTimer);
  if (view === "overview") loadSummary();
  if (view === "rooms") loadRooms();
  if (view === "bookings") loadBookings();
  if (view === "services") {
    loadServices();
    serviceTimer = setInterval(() => loadServices(true), 30000);
  }
  if (view === "history") loadHistory();
}

// ================= Tổng quan =================
function renderSummary(summary) {
  document.querySelector("#statTotalRooms").textContent = summary.total_rooms;
  document.querySelector("#statOccupiedRooms").textContent = summary.occupied_rooms;
  document.querySelector("#statOccupancy").textContent = `${summary.occupancy_rate_percent}%`;
  document.querySelector("#statPending").textContent = summary.pending_bookings;
  document.querySelector("#statToday").textContent = summary.bookings_created_today;
  document.querySelector("#statRevenue").textContent = formatMoney(summary.completed_revenue);
  document.querySelector("#occupancyBig").textContent = `${summary.occupancy_rate_percent}%`;
  document.querySelector("#occupancyProgress").style.width = `${Math.min(100, Math.max(0, summary.occupancy_rate_percent))}%`;
  document.querySelector("#occupancyDescription").textContent =
    `${summary.occupied_rooms}/${summary.total_rooms} phòng đang có khách theo lịch lưu trú hôm nay.`;
  setCount("#pendingNavCount", summary.pending_bookings);
}

async function loadSummary() {
  try {
    renderSummary(await apiRequest("/api/dashboard/summary"));
  } catch (error) {
    notify(`Không tải được dashboard: ${error.message}`);
  }
}

// ================= Phòng =================
let roomsById = {};

function renderRooms(rooms) {
  roomsById = Object.fromEntries(rooms.map(room => [String(room.id), room]));
  const board = document.querySelector("#roomBoard");
  const roomFilter = document.querySelector("#historyRoomFilter");
  const selected = roomFilter.value;
  roomFilter.innerHTML = '<option value="">Tất cả</option>' +
    rooms.map(room => `<option value="${Number(room.id)}">${escapeHtml(room.room_number)}</option>`).join("");
  roomFilter.value = selected;

  if (!rooms.length) {
    board.innerHTML = '<div class="surface-card loading-state">Chưa có phòng nào trong cơ sở dữ liệu.</div>';
    return;
  }
  board.innerHTML = rooms.map(room => {
    const status = room.operational_status;
    const roomNumber = escapeHtml(room.room_number);
    const guest = room.current_guest_name
      ? `<div class="room-guest">
           <strong>${escapeHtml(room.current_guest_name)}</strong>
           <small>${escapeHtml(room.current_guest_phone || "")}</small>
           <small>Trả phòng ${formatDate(room.current_check_out)}</small>
         </div>`
      : "";
    return `
      <article class="room-card ${escapeHtml(status)}">
        <div class="room-card-top">
          <div><div class="room-number">${roomNumber}</div><div class="room-floor">${room.floor ? `Tầng ${room.floor}` : "Chưa gán tầng"}</div></div>
          ${badge(status, roomStatusNames)}
        </div>
        <div class="room-type">${escapeHtml(room.room_type)}</div>
        <div class="room-meta">Tối đa ${Number(room.max_guests)} khách, ${formatMoney(room.price_per_night)}/đêm</div>
        ${guest}
        <div class="room-status-control">
          <label for="room-status-${Number(room.id)}">Cập nhật tình trạng</label>
          <select id="room-status-${Number(room.id)}" data-room-status data-room-id="${Number(room.id)}">
            ${Object.entries(roomStatusNames).map(([value, label]) =>
              `<option value="${value}" ${value === status ? "selected" : ""}>${label}</option>`).join("")}
          </select>
        </div>
        ${status === "occupied" || room.current_guest_name ? "" :
          `<button class="room-detail-button" type="button" data-walk-in="${Number(room.id)}">Thêm khách tại quầy</button>`}
        <button class="room-detail-button" type="button" data-room-detail="${Number(room.id)}">Khách và lịch sử phòng</button>
      </article>`;
  }).join("");
}

async function loadRooms() {
  const board = document.querySelector("#roomBoard");
  if (!board.children.length || board.querySelector(".loading-state")) {
    board.innerHTML = '<div class="surface-card loading-state">Đang tải danh sách phòng...</div>';
  }
  try {
    renderRooms(await apiRequest("/api/staff/rooms"));
  } catch (error) {
    board.innerHTML = `<div class="surface-card loading-state">${escapeHtml(error.message)}</div>`;
  }
}

function openWalkInForm(roomId) {
  const room = roomsById[String(roomId)];
  if (!room) return;
  drawerRoomId = roomId;
  manageBookingId = null;
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  openDrawer(
    `Thêm khách tại quầy, phòng ${room.room_number}`,
    `${escapeHtml(room.room_type)}, tối đa ${Number(room.max_guests)} khách, ${formatMoney(room.price_per_night)}/đêm`,
    `<section class="drawer-section manage-form-card">
      <form class="manage-form" data-form="walkin" data-room-id="${Number(roomId)}">
        <div class="form-grid">
          <label>Họ tên khách<input name="customer_name" type="text" minlength="2" maxlength="120" required autocomplete="name"></label>
          <label>Điện thoại<input name="customer_phone" type="tel" minlength="8" maxlength="20" required autocomplete="tel"></label>
        </div>
        <label>Email <small>(không bắt buộc)</small><input name="customer_email" type="email" maxlength="190" autocomplete="email"></label>
        <div class="form-grid">
          <label>Trả phòng<input name="check_out" type="date" min="${tomorrow}" value="${tomorrow}" required></label>
          <label>Số khách<input name="guests" type="number" min="1" max="${Number(room.max_guests)}" value="1" required></label>
        </div>
        <label>Ghi chú<textarea name="note" rows="2" maxlength="1000" placeholder="Ví dụ: khách trả tiền mặt, cần thêm giường phụ"></textarea></label>
        <p class="form-hint">Nhận phòng tính từ hôm nay. Khách dùng số điện thoại này để mở dịch vụ phòng.</p>
        <p class="form-preview" id="walkInPreview"></p>
        <p class="form-error" id="manageError" role="alert"></p>
        <div class="form-actions">
          <button class="primary-button" type="submit">Nhận khách</button>
          <button class="secondary-button" type="button" data-close-drawer>Bỏ qua</button>
        </div>
      </form>
    </section>`,
  );
  document.querySelector('[data-form="walkin"] input')?.focus();
  updateWalkInPreview();
}

function updateWalkInPreview() {
  const form = document.querySelector('[data-form="walkin"]');
  if (!form) return;
  const room = roomsById[String(form.dataset.roomId)];
  const nights = nightsBetween(todayISO(), form.check_out.value);
  document.querySelector("#walkInPreview").textContent = nights > 0
    ? `${nights} đêm × ${formatMoney(room.price_per_night)} = tiền phòng ${formatMoney(nights * Number(room.price_per_night))}.`
    : "Ngày trả phòng phải sau hôm nay.";
}

async function updateRoomStatus(roomId, status) {
  try {
    const params = new URLSearchParams({ operational_status: status });
    await apiRequest(`/api/rooms/${roomId}/operational-status?${params.toString()}`, { method: "PATCH" });
    notify(`Đã cập nhật trạng thái phòng thành: ${roomStatusNames[status]}.`);
  } catch (error) {
    notify(`Không thể cập nhật phòng: ${error.message}`);
  }
  await loadRooms();
}

// ================= Đơn đặt phòng =================
function customerCell(booking) {
  return `<span class="customer-name">${escapeHtml(booking.customer_name || "Khách hàng")}</span>
          <span class="subtext">${escapeHtml(booking.customer_phone || booking.customer_email || "Thông tin liên hệ cũ")}</span>`;
}
function roomCell(booking) {
  return `<strong>Phòng ${escapeHtml(booking.room_number || String(booking.room_id))}</strong><span class="subtext">${escapeHtml(booking.room_type || "")}</span>`;
}
function totalCell(booking) {
  const services = Number(booking.service_total || 0);
  const deposit = Number(booking.deposit_paid || 0);
  return `<strong>${formatMoney(booking.grand_total ?? booking.total_price)}</strong>
          ${services ? `<span class="subtext">Phòng ${formatMoney(booking.total_price)}</span><span class="subtext">Dịch vụ ${formatMoney(services)}</span>` : ""}
          ${deposit ? `<span class="subtext deposit-text">Đã cọc ${formatMoney(deposit)}</span>` : ""}`;
}

function stayCell(booking) {
  return `${formatDate(booking.check_in)}<span class="subtext">đến ${formatDate(booking.check_out)}, ${nightsBetween(booking.check_in, booking.check_out)} đêm</span>`;
}

function renderPending(bookings) {
  const body = document.querySelector("#bookingTableBody");
  setCount("#pendingNavCount", bookings.length);
  document.querySelector("#bookingSummary").innerHTML =
    `<strong>${bookings.length} yêu cầu đang chờ xử lý.</strong> Bấm Chi tiết để sửa, nhận cọc hoặc hủy đơn.`;
  if (!bookings.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-cell">Hiện chưa có yêu cầu đặt phòng nào cần xử lý.</td></tr>';
    return;
  }
  body.innerHTML = bookings.map(booking => `
    <tr>
      <td>${codeCell(booking.booking_code)}<span class="subtext">${formatDateTime(booking.created_at)}</span></td>
      <td>${customerCell(booking)}</td>
      <td>${roomCell(booking)}</td>
      <td>${stayCell(booking)}</td>
      <td>${Number(booking.guests)} người</td>
      <td>${totalCell(booking)}</td>
      <td><div class="booking-actions">${bookingActionButtons(booking)}</div></td>
    </tr>`).join("");
}

function renderActive(bookings) {
  const body = document.querySelector("#activeTableBody");
  if (!bookings.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-cell">Không có đơn nào đang chờ nhận khách hoặc đang lưu trú.</td></tr>';
    return;
  }
  body.innerHTML = bookings.map(booking => `
    <tr>
      <td>${codeCell(booking.booking_code)}<span class="subtext">${Number(booking.guests)} người</span></td>
      <td>${customerCell(booking)}</td>
      <td>${roomCell(booking)}</td>
      <td>${stayCell(booking)}</td>
      <td>${badge(booking.status)}</td>
      <td>${totalCell(booking)}</td>
      <td><div class="booking-actions">${bookingActionButtons(booking)}</div></td>
    </tr>`).join("");
}

async function loadBookings() {
  try {
    const [pending, active] = await Promise.all([
      apiRequest("/api/bookings/pending"),
      apiRequest("/api/bookings/active"),
    ]);
    renderPending(pending);
    renderActive(active);
  } catch (error) {
    document.querySelector("#bookingTableBody").innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    document.querySelector("#activeTableBody").innerHTML = "";
    document.querySelector("#bookingSummary").textContent = "Không tải được danh sách đơn.";
  }
}

async function updateBookingStatus(bookingId, status) {
  if (status === "checked_out") {
    openCheckoutDrawer(bookingId);
    return;
  }
  const verb = bookingActionVerbs[status] || "cập nhật";
  if (!window.confirm(`Bạn có chắc chắn muốn ${verb} đơn đặt phòng này?`)) return;
  try {
    await apiRequest(`/api/bookings/${bookingId}/status`, {
      method: "PATCH", body: JSON.stringify({ status }),
    });
    const done = { confirmed: "Đã xác nhận đơn.", rejected: "Đã từ chối đơn.", checked_in: "Đã nhận khách. Phòng chuyển sang Đang có khách.",
      checked_out: "Đã trả phòng. Phòng chuyển sang Chờ dọn.", cancelled: "Đã hủy đơn." };
    notify(done[status] || "Đã cập nhật đơn.");
    await refreshAfterChange();
  } catch (error) {
    notify(`Không thể cập nhật đơn: ${error.message}`);
  }
}

async function refreshAfterChange() {
  const jobs = [loadBookings(), loadRooms()];
  if (activeView === "history") jobs.push(loadHistory());
  if (currentUser.role === "admin") jobs.push(loadSummary());
  await Promise.all(jobs);
  if (drawerRoomId) openRoomDrawer(drawerRoomId);
  else if (manageBookingId && drawer.classList.contains("open")) openBookingManage(manageBookingId);
}

// ================= Dịch vụ phòng =================
function serviceItemsText(items) {
  return items.map(item => `${escapeHtml(item.name)} × ${Number(item.quantity)}`).join("<br>");
}

function serviceCard(request, compact = false) {
  const actions = (serviceActions[request.status] || []).map(([status, label, cls]) =>
    `<button class="${cls}" type="button" data-service-action="${status}" data-service-id="${Number(request.id)}">${label}</button>`).join("");
  return `
    <article class="service-card ${escapeHtml(request.status)}">
      <div class="service-card-head">
        <div>
          ${compact ? "" : `<span class="service-room">Phòng ${escapeHtml(request.room_number)}</span>`}
          <strong class="service-kind">${escapeHtml(request.category_label)}</strong>
        </div>
        ${badge(request.status, serviceStatusNames)}
      </div>
      <div class="service-items">${serviceItemsText(request.items)}</div>
      <div class="service-meta">Thời gian: <strong>${request.scheduled_at ? formatDateTime(request.scheduled_at) : "Cần ngay"}</strong></div>
      ${request.destination ? `<div class="service-meta">Điểm đến: <strong>${escapeHtml(request.destination)}</strong></div>` : ""}
      ${request.note ? `<div class="service-note">${escapeHtml(request.note)}</div>` : ""}
      <div class="service-foot">
        <span>${compact ? "" : `${escapeHtml(request.customer_name || "")}${request.customer_phone ? `, ${escapeHtml(request.customer_phone)}` : ""}<br>`}Gửi lúc ${formatDateTime(request.created_at)}</span>
        <strong>${Number(request.total_price) ? formatMoney(request.total_price) : "Miễn phí"}</strong>
      </div>
      ${actions ? `<div class="booking-actions service-actions">${actions}</div>` : ""}
    </article>`;
}

async function loadServices(silent = false) {
  const list = document.querySelector("#serviceList");
  if (!silent) list.innerHTML = '<div class="loading-state">Đang tải yêu cầu…</div>';
  try {
    const data = await apiRequest(`/api/service-requests?status=${encodeURIComponent(serviceFilter)}`);
    setCount("#serviceNavCount", data.pending_count);
    list.innerHTML = data.items.length
      ? data.items.map(item => serviceCard(item)).join("")
      : `<div class="loading-state">${serviceFilter === "open" ? "Không có yêu cầu nào cần xử lý." : "Chưa có yêu cầu nào trong mục này."}</div>`;
  } catch (error) {
    if (!silent) list.innerHTML = `<div class="loading-state">${escapeHtml(error.message)}</div>`;
  }
}

async function refreshServiceCount() {
  try {
    const data = await apiRequest("/api/service-requests?status=pending");
    setCount("#serviceNavCount", data.pending_count);
  } catch { /* bảng dịch vụ có thể chưa được tạo */ }
}

async function updateServiceStatus(requestId, status) {
  if (status === "cancelled" && !window.confirm("Hủy yêu cầu dịch vụ này?")) return;
  try {
    await apiRequest(`/api/service-requests/${requestId}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
    notify(status === "in_progress" ? "Đã tiếp nhận yêu cầu." : status === "completed" ? "Đã hoàn tất yêu cầu." : "Đã hủy yêu cầu.");
    await Promise.all([loadServices(true), loadBookings()]);
    if (activeView === "history") loadHistory();
    if (drawerRoomId) openRoomDrawer(drawerRoomId);
  } catch (error) {
    notify(`Không thể cập nhật yêu cầu: ${error.message}`);
  }
}

// ================= Lịch sử đơn =================
async function loadHistory() {
  const body = document.querySelector("#historyTableBody");
  const form = new FormData(document.querySelector("#historyFilter"));
  const params = new URLSearchParams({ page: historyPage, page_size: HISTORY_PAGE_SIZE });
  for (const [key, value] of form.entries()) if (value) params.set(key, value);
  body.innerHTML = '<tr><td colspan="7" class="empty-cell">Đang tải dữ liệu…</td></tr>';
  try {
    const data = await apiRequest(`/api/bookings/history?${params.toString()}`);
    historyRows = data.items;
    const pages = Math.max(1, Math.ceil(data.total / HISTORY_PAGE_SIZE));
    document.querySelector("#historyPagerInfo").textContent = `${data.total} đơn, trang ${historyPage}/${pages}`;
    document.querySelector("#historyPrev").disabled = historyPage <= 1;
    document.querySelector("#historyNext").disabled = historyPage >= pages;
    if (!data.items.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty-cell">Không có đơn nào khớp bộ lọc. Thử bỏ bớt điều kiện.</td></tr>';
      return;
    }
    body.innerHTML = data.items.map((booking, index) => `
      <tr class="clickable-row" data-history-index="${index}" tabindex="0">
        <td>${codeCell(booking.booking_code)}</td>
        <td>${customerCell(booking)}</td>
        <td>${roomCell(booking)}</td>
        <td>${stayCell(booking)}</td>
        <td>${badge(booking.status)}</td>
        <td>${totalCell(booking)}</td>
        <td>${formatDateTime(booking.created_at)}</td>
      </tr>`).join("");
  } catch (error) {
    body.innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
  }
}

// ================= Ngăn chi tiết =================
const drawer = document.querySelector("#detailDrawer");
const drawerScrim = document.querySelector("#drawerScrim");

function openDrawer(title, subHtml, bodyHtml) {
  if (!drawer.classList.contains("open")) lastFocus = document.activeElement;
  document.querySelector("#drawerTitle").textContent = title;
  document.querySelector("#drawerSub").innerHTML = subHtml;
  document.querySelector("#drawerBody").innerHTML = bodyHtml;
  drawer.classList.add("open");
  drawerScrim.classList.add("show");
  drawer.setAttribute("aria-hidden", "false");
  document.querySelector("#drawerClose").focus();
}

function closeDrawer() {
  drawer.classList.remove("open");
  drawerScrim.classList.remove("show");
  drawer.setAttribute("aria-hidden", "true");
  drawerRoomId = null;
  manageBookingId = null;
  if (lastFocus) lastFocus.focus();
}

function bookingInfoList(booking) {
  return `<dl class="info-list">
    <dt>Mã đặt phòng</dt><dd class="booking-code">${escapeHtml(booking.booking_code)}</dd>
    <dt>Họ tên</dt><dd>${escapeHtml(booking.customer_name || "—")}</dd>
    <dt>Điện thoại</dt><dd>${escapeHtml(booking.customer_phone || "—")}</dd>
    <dt>Email</dt><dd>${escapeHtml(booking.customer_email || "—")}</dd>
    <dt>Phòng</dt><dd>${escapeHtml(booking.room_number || "")} (${escapeHtml(booking.room_type || "")})</dd>
    <dt>Lưu trú</dt><dd>${formatDate(booking.check_in)} đến ${formatDate(booking.check_out)}, ${nightsBetween(booking.check_in, booking.check_out)} đêm</dd>
    <dt>Số khách</dt><dd>${Number(booking.guests)} người</dd>
    <dt>Tiền phòng</dt><dd>${formatMoney(booking.total_price)}</dd>
    <dt>Dịch vụ phòng</dt><dd>${formatMoney(booking.service_total)}</dd>
    <dt>Tổng cộng</dt><dd><strong>${formatMoney(booking.grand_total ?? booking.total_price)}</strong></dd>
    ${Number(booking.deposit_paid) ? `<dt>Đã đặt cọc</dt><dd>− ${formatMoney(booking.deposit_paid)}</dd>
    <dt>Còn phải thu</dt><dd><strong>${formatMoney(booking.amount_due)}</strong></dd>` : ""}
    <dt>Ghi chú</dt><dd>${escapeHtml(booking.note || "—")}</dd>
    <dt>Ngày đặt</dt><dd>${formatDateTime(booking.created_at)}</dd>
  </dl>`;
}

function openBookingDrawer(booking) {
  openBookingManage(Number(booking.id));
}

// ================= Chi tiết đơn: sửa, đặt cọc, hủy =================
function depositBlock(booking) {
  if (!booking.deposit_status) {
    return '<p class="muted">Đơn chưa đặt cọc.</p>';
  }
  return `<div class="deposit-card ${escapeHtml(booking.deposit_status)}">
    <div><strong>${formatMoney(booking.deposit_amount)}</strong>
      <span class="subtext">${escapeHtml(depositMethodNames[booking.deposit_method] || booking.deposit_method || "")}, ${formatDateTime(booking.deposit_received_at)}</span></div>
    <span class="status-badge ${booking.deposit_status === "paid" ? "confirmed" : booking.deposit_status === "applied" ? "checked_out" : booking.deposit_status === "refunded" ? "pending" : "cancelled"}">${escapeHtml(depositStatusNames[booking.deposit_status] || booking.deposit_status)}</span>
  </div>`;
}

function changeLog(changes) {
  if (!changes || !changes.length) return '<p class="muted">Chưa có thay đổi nào.</p>';
  return `<ol class="change-log">${changes.map(change => `
    <li><span class="change-time">${formatDateTime(change.created_at)}${change.staff_name ? `, ${escapeHtml(change.staff_name)}` : ""}</span>
        <span>${escapeHtml(change.detail)}</span></li>`).join("")}</ol>`;
}

function manageButtons(booking) {
  const open = ["pending", "confirmed", "checked_in"].includes(booking.status);
  let html = bookingActionButtons(booking, { withDetail: false });
  if (open) html += `<button type="button" data-manage="edit">${booking.status === "checked_in" ? "Gia hạn, sửa đơn" : "Sửa đơn"}</button>`;
  if (open && booking.deposit_status !== "applied") {
    html += `<button type="button" data-manage="deposit">${booking.deposit_status === "paid" ? "Sửa tiền cọc" : "Nhận đặt cọc"}</button>`;
  }
  if (["pending", "confirmed"].includes(booking.status)) html += '<button class="reject" type="button" data-manage="cancel">Hủy đơn</button>';
  if (booking.status === "checked_out") html += `<button class="approve" type="button" data-view-invoice="${Number(booking.id)}">Xem hóa đơn</button>`;
  return html;
}

function renderManageView(booking) {
  manageBooking = booking;
  document.querySelector("#drawerTitle").textContent = `Đơn phòng ${booking.room_number}`;
  document.querySelector("#drawerSub").innerHTML = `${escapeHtml(booking.room_type)} ${badge(booking.status)}`;
  document.querySelector("#drawerBody").innerHTML = `
    <section class="drawer-section">
      <div class="booking-actions drawer-actions manage-actions">${manageButtons(booking)}</div>
    </section>
    <div id="manageForm"></div>
    <section class="drawer-section">${bookingInfoList(booking)}</section>
    <section class="drawer-section"><h3>Đặt cọc</h3>${depositBlock(booking)}</section>
    <section class="drawer-section"><h3>Lịch sử thay đổi</h3>${changeLog(booking.changes)}</section>`;
}

async function openBookingManage(bookingId) {
  drawerRoomId = null;
  manageBookingId = bookingId;
  if (!drawer.classList.contains("open")) openDrawer("Đang tải…", "", '<div class="loading-state">Đang tải đơn…</div>');
  try {
    const booking = await apiRequest(`/api/bookings/${bookingId}`);
    if (manageBookingId === bookingId) renderManageView(booking);
  } catch (error) {
    document.querySelector("#drawerBody").innerHTML = `<div class="loading-state">${escapeHtml(error.message)}</div>`;
  }
}

function showManageForm(html) {
  const box = document.querySelector("#manageForm");
  box.innerHTML = `<section class="drawer-section manage-form-card">${html}</section>`;
  box.querySelector("input, select, textarea")?.focus();
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderEditForm(booking) {
  const staying = booking.status === "checked_in";
  showManageForm(`
    <h3>${staying ? "Gia hạn hoặc sửa đơn" : "Sửa đơn"}</h3>
    <form class="manage-form" data-form="edit">
      <div class="form-grid">
        <label>Nhận phòng<input name="check_in" type="date" value="${String(booking.check_in).slice(0, 10)}" ${staying ? "readonly" : `min="${todayISO()}"`} required></label>
        <label>Trả phòng<input name="check_out" type="date" value="${String(booking.check_out).slice(0, 10)}" required></label>
        <label>Số khách<input name="guests" type="number" min="1" max="20" value="${Number(booking.guests)}" required></label>
        <label>Phòng
          <select name="room_id" ${staying ? "disabled" : ""}>
            <option value="${Number(booking.room_id)}" data-price="${Number(booking.base_price)}">Phòng ${escapeHtml(booking.room_number)}, ${escapeHtml(booking.room_type)} (hiện tại)</option>
          </select>
        </label>
      </div>
      <div class="form-grid">
        <label>Họ tên khách<input name="customer_name" type="text" minlength="2" maxlength="120" value="${escapeHtml(booking.customer_name || "")}"></label>
        <label>Điện thoại<input name="customer_phone" type="tel" minlength="8" maxlength="20" value="${escapeHtml(booking.customer_phone || "")}"></label>
      </div>
      <label>Email<input name="customer_email" type="email" maxlength="190" value="${escapeHtml(booking.customer_email || "")}"></label>
      <label>Ghi chú<textarea name="note" rows="2" maxlength="1000">${escapeHtml(booking.note || "")}</textarea></label>
      <p class="form-preview" id="editPreview"></p>
      <p class="form-error" id="manageError" role="alert"></p>
      <div class="form-actions">
        <button class="primary-button" type="submit">Lưu thay đổi</button>
        <button class="secondary-button" type="button" data-manage="close">Bỏ qua</button>
      </div>
    </form>`);
  if (!staying) loadRoomOptions();
  updateEditPreview();
}

async function loadRoomOptions() {
  const form = document.querySelector('[data-form="edit"]');
  if (!form) return;
  const checkIn = form.check_in.value;
  const checkOut = form.check_out.value;
  if (!checkIn || !checkOut || checkOut <= checkIn) return;
  try {
    const rooms = await apiRequest(`/api/bookings/${manageBooking.id}/room-options?check_in=${checkIn}&check_out=${checkOut}`);
    const select = form.room_id;
    const current = Number(select.value);
    const currentFree = rooms.some(room => Number(room.id) === Number(manageBooking.room_id));
    select.innerHTML = rooms.map(room => `
      <option value="${Number(room.id)}" data-price="${Number(room.price_per_night)}" data-max="${Number(room.max_guests)}">
        Phòng ${escapeHtml(room.room_number)}, ${escapeHtml(room.room_type)}${Number(room.id) === Number(manageBooking.room_id) ? " (hiện tại)" : ""}
      </option>`).join("") || '<option value="">Không còn phòng trống cho ngày này</option>';
    if (rooms.some(room => Number(room.id) === current)) select.value = String(current);
    if (!currentFree && rooms.length) {
      document.querySelector("#manageError").textContent =
        `Phòng ${manageBooking.room_number} đã có đơn khác trong ngày mới. Hãy chọn phòng khác ở trên.`;
    } else {
      document.querySelector("#manageError").textContent = "";
    }
  } catch (error) {
    document.querySelector("#manageError").textContent = error.message;
  }
  updateEditPreview();
}

function updateEditPreview() {
  const form = document.querySelector('[data-form="edit"]');
  if (!form) return;
  const nights = nightsBetween(form.check_in.value, form.check_out.value);
  const option = form.room_id.selectedOptions[0];
  const price = Number(option?.dataset.price || manageBooking.base_price || 0);
  document.querySelector("#editPreview").textContent = nights > 0
    ? `${nights} đêm × ${formatMoney(price)} = tiền phòng mới ${formatMoney(nights * price)} (hiện tại ${formatMoney(manageBooking.total_price)}).`
    : "Ngày trả phòng phải sau ngày nhận phòng.";
}

function renderDepositForm(booking) {
  const suggested = Number(booking.deposit_amount) || Math.round(Number(booking.total_price) * 0.3 / 100000) * 100000;
  showManageForm(`
    <h3>${booking.deposit_status === "paid" ? "Sửa tiền cọc" : "Nhận đặt cọc"}</h3>
    <form class="manage-form" data-form="deposit">
      <div class="form-grid">
        <label>Số tiền (₫)<input name="amount" type="number" min="1000" step="1000" max="${Number(booking.total_price)}" value="${suggested || ""}" required></label>
        <label>Hình thức
          <select name="method">
            ${Object.entries(depositMethodNames).map(([value, label]) =>
              `<option value="${value}" ${booking.deposit_method === value ? "selected" : ""}>${label}</option>`).join("")}
          </select>
        </label>
      </div>
      <label>Ghi chú<input name="note" type="text" maxlength="255" placeholder="Ví dụ: mã giao dịch, người nộp"></label>
      ${booking.status === "pending" ? '<label class="check-row"><input name="confirm_booking" type="checkbox" checked> Xác nhận đơn luôn sau khi nhận cọc</label>' : ""}
      <p class="form-hint">Gợi ý 30% tiền phòng. Tiền cọc tối đa bằng tiền phòng (${formatMoney(booking.total_price)}) và được trừ vào hóa đơn khi trả phòng.</p>
      <p class="form-error" id="manageError" role="alert"></p>
      <div class="form-actions">
        <button class="primary-button" type="submit">Lưu tiền cọc</button>
        <button class="secondary-button" type="button" data-manage="close">Bỏ qua</button>
      </div>
    </form>`);
}

function renderCancelForm(booking) {
  const hasDeposit = booking.deposit_status === "paid";
  showManageForm(`
    <h3>Hủy đơn của ${escapeHtml(booking.customer_name || "khách")}?</h3>
    <form class="manage-form" data-form="cancel">
      <label>Lý do hủy<textarea name="reason" rows="2" minlength="3" maxlength="255" required placeholder="Ví dụ: khách đổi kế hoạch, gọi điện báo hủy"></textarea></label>
      ${hasDeposit ? `
        <fieldset class="radio-group">
          <legend>Tiền cọc ${formatMoney(booking.deposit_amount)}</legend>
          <label class="check-row"><input type="radio" name="deposit_action" value="refund" required> Hoàn cọc cho khách</label>
          <label class="check-row"><input type="radio" name="deposit_action" value="keep"> Giữ cọc theo chính sách hủy</label>
        </fieldset>` : ""}
      <p class="form-hint">Phòng được trả lại cho khách khác đặt. Không hoàn tác được.</p>
      <p class="form-error" id="manageError" role="alert"></p>
      <div class="form-actions">
        <button class="primary-button danger-button" type="submit">Hủy đơn này</button>
        <button class="secondary-button" type="button" data-manage="close">Giữ đơn</button>
      </div>
    </form>`);
}

async function submitManageForm(form) {
  const kind = form.dataset.form;
  const errorBox = form.querySelector("#manageError");
  const submit = form.querySelector('[type="submit"]');
  errorBox.textContent = "";
  let url, method, body, done;
  if (kind === "walkin") {
    if (form.check_out.value <= todayISO()) { errorBox.textContent = "Ngày trả phòng phải sau hôm nay."; return; }
    url = "/api/staff/walk-ins"; method = "POST";
    body = {
      room_id: Number(form.dataset.roomId),
      customer_name: form.customer_name.value.trim(),
      customer_phone: form.customer_phone.value.trim(),
      customer_email: form.customer_email.value.trim() || null,
      check_out: form.check_out.value,
      guests: Number(form.guests.value),
      note: form.note.value.trim() || null,
    };
    done = "Đã nhận khách tại quầy. Phòng đã khóa, khách khác không đặt được.";
  } else if (kind === "edit") {
    if (form.check_out.value <= form.check_in.value) { errorBox.textContent = "Ngày trả phòng phải sau ngày nhận phòng."; return; }
    url = `/api/bookings/${manageBooking.id}`; method = "PATCH";
    body = {
      check_in: form.check_in.value, check_out: form.check_out.value, guests: Number(form.guests.value),
      room_id: form.room_id.disabled || !form.room_id.value ? null : Number(form.room_id.value),
      note: form.note.value.trim() || null,
      customer_name: form.customer_name.value.trim() || null,
      customer_phone: form.customer_phone.value.trim() || null,
      customer_email: form.customer_email.value.trim() || null,
    };
    done = "Đã lưu thay đổi của đơn.";
  } else if (kind === "deposit") {
    url = `/api/bookings/${manageBooking.id}/deposit`; method = "POST";
    body = {
      amount: Number(form.amount.value), method: form.method.value,
      note: form.note.value.trim() || null,
      confirm_booking: Boolean(form.confirm_booking?.checked),
    };
    done = body.confirm_booking ? "Đã nhận cọc và xác nhận đơn." : "Đã lưu tiền cọc.";
  } else {
    url = `/api/bookings/${manageBooking.id}/cancel`; method = "POST";
    const action = form.querySelector('[name="deposit_action"]:checked');
    body = { reason: form.reason.value.trim(), deposit_action: action ? action.value : null };
    done = "Đã hủy đơn. Phòng đã được trả lại để khách khác đặt.";
  }
  submit.disabled = true;
  try {
    await apiRequest(url, { method, body: JSON.stringify(body) });
    notify(done);
    await refreshAfterChange();
  } catch (error) {
    errorBox.textContent = error.message;
    submit.disabled = false;
  }
}

function stayCard(booking, highlight = false) {
  let actions = bookingActionButtons(booking);
  if (booking.status === "checked_out") {
    actions += `<button type="button" data-view-invoice="${Number(booking.id)}">Xem hóa đơn</button>`;
  }
  if (Number(booking.deposit_paid)) booking = { ...booking, note: [booking.note, `Đã cọc ${formatMoney(booking.deposit_paid)}`].filter(Boolean).join(". ") };
  return `<article class="stay-card ${highlight ? "current" : ""}">
    <div class="stay-card-head"><strong>${escapeHtml(booking.customer_name || "Khách hàng")}</strong>${badge(booking.status)}</div>
    <div class="subtext">${escapeHtml(booking.customer_phone || "")}${booking.customer_email ? `, ${escapeHtml(booking.customer_email)}` : ""}</div>
    <div class="stay-dates">${formatDate(booking.check_in)} đến ${formatDate(booking.check_out)}, ${Number(booking.guests)} khách, ${formatMoney(booking.grand_total ?? booking.total_price)}${Number(booking.service_total) ? ` (gồm ${formatMoney(booking.service_total)} dịch vụ)` : ""}</div>
    ${booking.note ? `<div class="subtext">Ghi chú: ${escapeHtml(booking.note)}</div>` : ""}
    ${actions ? `<div class="booking-actions drawer-actions">${actions}</div>` : ""}
  </article>`;
}

async function openRoomDrawer(roomId) {
  manageBookingId = null;
  const wasOpen = drawer.classList.contains("open") && drawerRoomId === roomId;
  drawerRoomId = roomId;
  if (!wasOpen) openDrawer("Đang tải…", "", '<div class="loading-state">Đang tải thông tin phòng…</div>');
  try {
    const detail = await apiRequest(`/api/staff/rooms/${roomId}/detail`);
    if (drawerRoomId !== roomId) return;
    const room = detail.room;
    document.querySelector("#drawerTitle").textContent = `Phòng ${room.room_number}`;
    document.querySelector("#drawerSub").innerHTML =
      `${escapeHtml(room.room_type)}, ${room.floor ? `tầng ${room.floor}` : "chưa gán tầng"} ${badge(room.operational_status, roomStatusNames)}`;
    const services = detail.current_services || [];
    document.querySelector("#drawerBody").innerHTML = `
      <section class="drawer-section">
        <h3>Khách đang ở</h3>
        ${detail.current_booking ? stayCard(detail.current_booking, true) : '<p class="muted">Phòng hiện không có khách lưu trú.</p>'}
      </section>
      ${detail.current_booking ? `
      <section class="drawer-section">
        <h3>Dịch vụ trong lượt ở này</h3>
        ${services.length
          ? services.map(item => serviceCard(item, true)).join("") +
            `<p class="drawer-total">Cộng vào hóa đơn phòng: <strong>${formatMoney(detail.current_services_total)}</strong></p>`
          : '<p class="muted">Khách chưa gọi dịch vụ nào.</p>'}
      </section>` : ""}
      <section class="drawer-section">
        <h3>Đơn sắp tới (${detail.upcoming_bookings.length})</h3>
        ${detail.upcoming_bookings.map(b => stayCard(b)).join("") || '<p class="muted">Chưa có đơn nào sắp tới.</p>'}
      </section>
      <section class="drawer-section">
        <h3>Lịch sử phòng (${detail.total_stays} lượt lưu trú)</h3>
        ${detail.history.map(b => stayCard(b)).join("") || '<p class="muted">Phòng chưa có lịch sử đặt.</p>'}
      </section>`;
  } catch (error) {
    document.querySelector("#drawerBody").innerHTML = `<div class="loading-state">${escapeHtml(error.message)}</div>`;
  }
}

// ================= Hóa đơn =================
let currentInvoice = null;

function invoiceDate(value) {
  return value ? formatDate(value) : "—";
}

function renderInvoice(invoice, { draft = false } = {}) {
  const services = invoice.service_lines || [];
  const excluded = invoice.excluded_pending || [];
  const room = invoice.room_line;
  return `
    <article class="invoice ${draft ? "invoice-draft" : ""}" aria-label="${draft ? "Hóa đơn tạm tính" : "Hóa đơn"}">
      <header class="invoice-head">
        <div class="invoice-brand">
          <span class="invoice-brand-name">Grandview</span>
          <span class="invoice-brand-sub">Khách sạn và nghỉ dưỡng</span>
        </div>
        <div class="invoice-id">
          <h3>${draft ? "Hóa đơn tạm tính" : "Hóa đơn thanh toán"}</h3>
          ${draft
            ? '<span class="status-badge pending">Chưa xuất</span>'
            : `<dl>
                 <dt>Số</dt><dd>${escapeHtml(invoice.invoice_number)}</dd>
                 <dt>Ngày xuất</dt><dd>${formatDateTime(invoice.issued_at)}</dd>
                 ${invoice.issued_by_name ? `<dt>Lễ tân</dt><dd>${escapeHtml(invoice.issued_by_name)}</dd>` : ""}
               </dl>`}
        </div>
      </header>

      <div class="invoice-parties">
        <section>
          <h4>Khách hàng</h4>
          <p><strong>${escapeHtml(invoice.customer_name || "Khách hàng")}</strong></p>
          <p>${escapeHtml(invoice.customer_phone || "")}</p>
          <p>${escapeHtml(invoice.customer_email || "")}</p>
        </section>
        <section>
          <h4>Lưu trú</h4>
          <p><strong>Phòng ${escapeHtml(invoice.room_number)}</strong>, ${escapeHtml(invoice.room_type)}</p>
          <p>${invoiceDate(invoice.check_in)} đến ${invoiceDate(invoice.check_out)}, ${Number(invoice.nights)} đêm</p>
          <p>Mã đặt phòng: <span class="booking-code">${escapeHtml(invoice.booking_code)}</span></p>
        </section>
      </div>

      <table class="invoice-table">
        <thead>
          <tr><th scope="col">Nội dung</th><th scope="col" class="num">SL</th><th scope="col" class="num">Đơn giá</th><th scope="col" class="num">Thành tiền</th></tr>
        </thead>
        <tbody>
          <tr class="invoice-group"><th colspan="4" scope="rowgroup">Tiền phòng</th></tr>
          <tr>
            <td>${escapeHtml(room.description)}<span class="subtext">${escapeHtml(room.detail)}</span></td>
            <td class="num">${Number(room.quantity)} ${escapeHtml(room.unit)}</td>
            <td class="num">${formatMoney(room.unit_price)}</td>
            <td class="num">${formatMoney(room.amount)}</td>
          </tr>
          <tr class="invoice-group"><th colspan="4" scope="rowgroup">Dịch vụ phòng</th></tr>
          ${services.length ? services.map(line => `
            <tr>
              <td>${escapeHtml(line.description)}<span class="subtext">${escapeHtml(line.category)}, ${formatDateTime(line.date)}</span></td>
              <td class="num">${Number(line.quantity)}</td>
              <td class="num">${Number(line.unit_price) ? formatMoney(line.unit_price) : "Miễn phí"}</td>
              <td class="num">${formatMoney(line.amount)}</td>
            </tr>`).join("") : '<tr><td colspan="4" class="invoice-none">Không sử dụng dịch vụ phòng.</td></tr>'}
        </tbody>
      </table>

      <dl class="invoice-totals">
        <dt>Tiền phòng</dt><dd>${formatMoney(invoice.room_amount)}</dd>
        <dt>Dịch vụ phòng</dt><dd>${formatMoney(invoice.service_amount)}</dd>
        <dt class="invoice-grand">Tổng cộng</dt><dd class="invoice-grand">${formatMoney(invoice.total_amount)}</dd>
        ${Number(invoice.deposit_paid) ? `
          <dt>Đã đặt cọc${invoice.deposit_method ? ` (${escapeHtml(depositMethodNames[invoice.deposit_method] || invoice.deposit_method)})` : ""}</dt><dd>− ${formatMoney(invoice.deposit_paid)}</dd>
          <dt class="invoice-due">Còn phải thanh toán</dt><dd class="invoice-due">${formatMoney(invoice.amount_due)}</dd>` : ""}
      </dl>

      ${draft && excluded.length ? `
        <div class="invoice-warning" role="note">
          <strong>${excluded.length} mục dịch vụ chưa được tiếp nhận sẽ không tính tiền</strong>
          <span>${excluded.map(line => `${escapeHtml(line.description)} × ${Number(line.quantity)}`).join(", ")}. Khi trả phòng, các yêu cầu này tự chuyển sang Đã hủy. Nếu khách đã được phục vụ, hãy bấm Tiếp nhận rồi Hoàn tất ở mục Dịch vụ phòng trước.</span>
        </div>` : ""}

      <footer class="invoice-foot">Cảm ơn quý khách đã lưu trú tại Grandview.</footer>
    </article>`;
}

async function openCheckoutDrawer(bookingId) {
  drawerRoomId = null;
  openDrawer("Trả phòng", "Kiểm tra hóa đơn với khách trước khi xác nhận.", '<div class="loading-state">Đang tính hóa đơn…</div>');
  try {
    const invoice = await apiRequest(`/api/bookings/${bookingId}/invoice-preview`);
    document.querySelector("#drawerTitle").textContent = `Trả phòng ${invoice.room_number}`;
    document.querySelector("#drawerBody").innerHTML = `
      ${renderInvoice(invoice, { draft: true })}
      <div class="invoice-actions">
        <button class="primary-button" type="button" data-confirm-checkout="${Number(bookingId)}">Xác nhận trả phòng và xuất hóa đơn</button>
        <button class="secondary-button" type="button" data-close-drawer>Để sau</button>
      </div>`;
  } catch (error) {
    document.querySelector("#drawerBody").innerHTML = `<div class="loading-state">${escapeHtml(error.message)}</div>`;
  }
}

async function confirmCheckout(bookingId, button) {
  button.disabled = true;
  button.textContent = "Đang xuất hóa đơn...";
  try {
    await apiRequest(`/api/bookings/${bookingId}/status`, {
      method: "PATCH", body: JSON.stringify({ status: "checked_out" }),
    });
    notify("Đã trả phòng và xuất hóa đơn. Phòng chuyển sang Chờ dọn.");
    await showInvoice(bookingId);
    const jobs = [loadBookings(), loadRooms(), loadServices(true)];
    if (activeView === "history") jobs.push(loadHistory());
    if (currentUser.role === "admin") jobs.push(loadSummary());
    await Promise.all(jobs);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Xác nhận trả phòng và xuất hóa đơn";
    notify(`Không thể trả phòng: ${error.message}`);
  }
}

async function showInvoice(bookingId) {
  drawerRoomId = null;
  if (!drawer.classList.contains("open")) openDrawer("Hóa đơn", "", '<div class="loading-state">Đang tải hóa đơn…</div>');
  try {
    const invoice = await apiRequest(`/api/bookings/${bookingId}/invoice`);
    currentInvoice = invoice;
    document.querySelector("#drawerTitle").textContent = `Hóa đơn ${invoice.invoice_number}`;
    document.querySelector("#drawerSub").innerHTML = `Phòng ${escapeHtml(invoice.room_number)} ${badge("checked_out")}`;
    document.querySelector("#drawerBody").innerHTML = `
      ${renderInvoice(invoice)}
      <div class="invoice-actions">
        <button class="primary-button" type="button" data-print-invoice>In hóa đơn</button>
      </div>`;
  } catch (error) {
    document.querySelector("#drawerTitle").textContent = "Hóa đơn";
    document.querySelector("#drawerBody").innerHTML = `<div class="loading-state">${escapeHtml(error.message)}</div>`;
  }
}

function printInvoice() {
  if (!currentInvoice) return;
  const area = document.querySelector("#printArea");
  area.innerHTML = renderInvoice(currentInvoice);
  const previousTitle = document.title;
  document.title = `Hoa-don-${currentInvoice.invoice_number}`;
  window.print();
  document.title = previousTitle;
}

// ================= Khởi động =================
async function initialize() {
  if (!token) {
    window.location.replace("/");
    return;
  }
  try {
    currentUser = await apiRequest("/api/auth/me");
  } catch (error) {
    notify(error.message);
    return;
  }
  if (!["admin", "receptionist"].includes(currentUser.role)) {
    window.location.replace("/");
    return;
  }

  document.querySelector("#userName").textContent = currentUser.full_name;
  document.querySelector("#userRole").textContent = roleNames[currentUser.role] || currentUser.role;
  document.querySelector("#userInitial").textContent = (currentUser.full_name || "G").trim().charAt(0).toUpperCase();
  document.querySelector("#todayLabel").textContent = new Intl.DateTimeFormat("vi-VN", {
    weekday: "long", day: "2-digit", month: "2-digit", year: "numeric",
  }).format(new Date());

  if (currentUser.role !== "admin") {
    document.querySelector("#overviewNav").hidden = true;
    setView("rooms");
  } else {
    setView("overview");
  }
  if (activeView !== "bookings") loadBookings();
  if (activeView !== "rooms") loadRooms();
  refreshServiceCount();
  setInterval(() => { if (activeView !== "services") refreshServiceCount(); }, 60000);
}

document.querySelectorAll("[data-view]").forEach(button => {
  button.addEventListener("click", () => setView(button.dataset.view));
});
document.querySelectorAll("[data-open-view]").forEach(button => {
  button.addEventListener("click", () => setView(button.dataset.openView));
});
document.querySelector("#refreshRooms").addEventListener("click", loadRooms);
document.querySelector("#refreshBookings").addEventListener("click", loadBookings);
document.querySelector("#refreshServices").addEventListener("click", () => loadServices());

// Thao tác đơn: dùng chung cho bảng đơn chờ, bảng đang lưu trú và ngăn chi tiết
document.addEventListener("click", event => {
  const bookingButton = event.target.closest("[data-booking-action]");
  if (bookingButton) {
    updateBookingStatus(Number(bookingButton.dataset.bookingId), bookingButton.dataset.bookingAction);
    return;
  }
  const serviceButton = event.target.closest("[data-service-action]");
  if (serviceButton) {
    updateServiceStatus(Number(serviceButton.dataset.serviceId), serviceButton.dataset.serviceAction);
    return;
  }
  const openBooking = event.target.closest("[data-open-booking]");
  if (openBooking) { openBookingManage(Number(openBooking.dataset.openBooking)); return; }
  const manage = event.target.closest("[data-manage]");
  if (manage && manageBooking) {
    const kind = manage.dataset.manage;
    if (kind === "edit") renderEditForm(manageBooking);
    else if (kind === "deposit") renderDepositForm(manageBooking);
    else if (kind === "cancel") renderCancelForm(manageBooking);
    else document.querySelector("#manageForm").innerHTML = "";
    return;
  }
  const walkInButton = event.target.closest("[data-walk-in]");
  if (walkInButton) { openWalkInForm(Number(walkInButton.dataset.walkIn)); return; }
  const roomButton = event.target.closest("[data-room-detail]");
  if (roomButton) { openRoomDrawer(Number(roomButton.dataset.roomDetail)); return; }
  const invoiceButton = event.target.closest("[data-view-invoice]");
  if (invoiceButton) { showInvoice(Number(invoiceButton.dataset.viewInvoice)); return; }
  const confirmButton = event.target.closest("[data-confirm-checkout]");
  if (confirmButton) { confirmCheckout(Number(confirmButton.dataset.confirmCheckout), confirmButton); return; }
  if (event.target.closest("[data-print-invoice]")) { printInvoice(); return; }
  if (event.target.closest("[data-close-drawer]")) closeDrawer();
});

document.querySelector("#drawerBody").addEventListener("submit", event => {
  const form = event.target.closest("[data-form]");
  if (!form) return;
  event.preventDefault();
  submitManageForm(form);
});
document.querySelector("#drawerBody").addEventListener("change", event => {
  if (event.target.closest('[data-form="walkin"]')) { updateWalkInPreview(); return; }
  const form = event.target.closest('[data-form="edit"]');
  if (!form) return;
  if (event.target.name === "check_in" || event.target.name === "check_out") loadRoomOptions();
  else updateEditPreview();
});

document.querySelector("#roomBoard").addEventListener("change", event => {
  const select = event.target.closest("[data-room-status]");
  if (!select) return;
  const roomId = Number(select.dataset.roomId);
  const room = roomsById[String(roomId)];
  // Đánh dấu "Đang có khách" cần thông tin khách và ngày trả phòng, nếu không
  // hệ thống vẫn bán phòng này cho khách đặt trên website.
  if (select.value === "occupied" && room && !room.current_guest_name) {
    select.value = room.operational_status;
    openWalkInForm(roomId);
    return;
  }
  updateRoomStatus(roomId, select.value);
});

document.querySelector("#serviceFilter").addEventListener("click", event => {
  const button = event.target.closest("[data-service-filter]");
  if (!button) return;
  serviceFilter = button.dataset.serviceFilter;
  document.querySelectorAll("[data-service-filter]").forEach(b => b.setAttribute("aria-pressed", String(b === button)));
  loadServices();
});

document.querySelector("#historyFilter").addEventListener("submit", event => {
  event.preventDefault();
  historyPage = 1;
  loadHistory();
});
document.querySelector("#historyFilter").addEventListener("reset", () => {
  setTimeout(() => { historyPage = 1; loadHistory(); });
});
document.querySelector("#historyPrev").addEventListener("click", () => { historyPage -= 1; loadHistory(); });
document.querySelector("#historyNext").addEventListener("click", () => { historyPage += 1; loadHistory(); });
const historyBody = document.querySelector("#historyTableBody");
historyBody.addEventListener("click", event => {
  const row = event.target.closest("[data-history-index]");
  if (row) openBookingDrawer(historyRows[Number(row.dataset.historyIndex)]);
});
historyBody.addEventListener("keydown", event => {
  const row = event.target.closest("[data-history-index]");
  if (row && event.key === "Enter") openBookingDrawer(historyRows[Number(row.dataset.historyIndex)]);
});

document.querySelector("#drawerClose").addEventListener("click", closeDrawer);
drawerScrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
});

document.querySelector("#logoutButton").addEventListener("click", () => {
  sessionStorage.removeItem("hotel_access_token");
  window.location.replace("/");
});
document.querySelector("#mobileMenu").addEventListener("click", () => {
  document.querySelector("#sidebar").classList.add("open");
  document.querySelector("#sidebarScrim").classList.add("show");
});
document.querySelector("#sidebarScrim").addEventListener("click", () => {
  document.querySelector("#sidebar").classList.remove("open");
  document.querySelector("#sidebarScrim").classList.remove("show");
});

initialize();
