const menuToggle = document.querySelector("#menuToggle");
const nav = document.querySelector("#mainNav");
const toast = document.querySelector("#toast");
const checkin = document.querySelector("#checkin");
const checkout = document.querySelector("#checkout");
const roomGrid = document.querySelector(".room-grid");
const authModal = document.querySelector("#authModal");
const authForm = document.querySelector("#authForm");
const authError = document.querySelector("#authError");
const authSubmit = document.querySelector("#authSubmit");
const accountBtn = document.querySelector("#accountBtn");
const guestBookingModal = document.querySelector("#guestBookingModal");
const guestBookingForm = document.querySelector("#guestBookingForm");
const guestBookingError = document.querySelector("#guestBookingError");
const guestBookingSubmit = document.querySelector("#guestBookingSubmit");
const bookingSuccessModal = document.querySelector("#bookingSuccessModal");
const bookingLookupForm = document.querySelector("#bookingLookupForm");
const bookingLookupPhone = document.querySelector("#bookingLookupPhone");
const bookingLookupError = document.querySelector("#bookingLookupError");
const bookingLookupResult = document.querySelector("#bookingLookupResult");
const bookingLookupSubmit = document.querySelector("#bookingLookupSubmit");

let currentUser = null;
let selectedRoom = null;
let toastTimer;

menuToggle.addEventListener("click", () => {
  nav.classList.toggle("open");
  menuToggle.setAttribute("aria-expanded", nav.classList.contains("open"));
});

document.querySelectorAll("#mainNav a").forEach(link => {
  link.addEventListener("click", () => nav.classList.remove("open"));
});

const today = new Date();
const tomorrow = new Date(today);
tomorrow.setDate(today.getDate() + 1);
const dayAfter = new Date(today);
dayAfter.setDate(today.getDate() + 2);
const asDateInput = date => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
};
checkin.min = asDateInput(today);
checkin.value = asDateInput(tomorrow);
checkout.min = asDateInput(dayAfter);
checkout.value = asDateInput(dayAfter);

checkin.addEventListener("change", () => {
  const next = new Date(`${checkin.value}T12:00:00`);
  next.setDate(next.getDate() + 1);
  checkout.min = asDateInput(next);
  if (checkout.value <= checkin.value) checkout.value = asDateInput(next);
});

function notify(message) {
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 5000);
}

function guestCount() {
  const match = document.querySelector("#guests").value.match(/^\s*(\d+)/);
  return match ? Number(match[1]) : 2;
}

async function apiRequest(url, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = sessionStorage.getItem("hotel_access_token");
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(url, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = Array.isArray(data.detail)
      ? data.detail.map(item => item.msg || "Lỗi dữ liệu").join("; ")
      : (data.detail || "Có lỗi xảy ra khi gọi API.");
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return data;
}

function formatPrice(value) {
  return new Intl.NumberFormat("vi-VN", {
    style: "currency",
    currency: "VND",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

const roomsStatus = document.querySelector("#roomsStatus");
const ARROW_ICON = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M7 17L17 7M9 7h8v8"/></svg>';
const PERSON_ICON = '<svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.5"/><path d="M5 20c1.2-3.6 4-5 7-5s5.8 1.4 7 5"/></svg>';

let roomTypes = [];
let preferredTypeId = null;
const FALLBACK_ROOM_IMAGE = "/images/phong-deluxe.svg";

// Mặc định: chỉ hiện hạng phòng, không lộ số phòng
const roomsTitle = document.querySelector("#roomsTitle");

function renderRoomTypes(types) {
  roomTypes = types;
  roomsTitle.innerHTML = 'Các hạng phòng <em class="script">được yêu thích</em>';
  if (!types.length) {
    roomGrid.innerHTML = '<p class="room-empty">Hiện chưa có hạng phòng nào mở đặt. Vui lòng quay lại sau.</p>';
    roomsStatus.textContent = "";
    return;
  }
  roomsStatus.textContent = "Chọn ngày ở thanh tìm phòng để xem phòng còn trống và số phòng cụ thể.";
  roomGrid.innerHTML = types.map(type => {
    const name = escapeHtml(type.name);
    return `
      <article class="room-card is-bookable" data-type-id="${Number(type.id)}">
        <img src="${escapeHtml(type.image_url || FALLBACK_ROOM_IMAGE)}" alt="${name} tại Grandview" width="800" height="1000" loading="lazy">
        <button class="room-arrow" type="button" data-pick-type="${Number(type.id)}" aria-label="Chọn ngày cho ${name}">${ARROW_ICON}</button>
        <div class="room-overlay">
          <ul class="chip-row">
            <li class="chip chip-glass">${PERSON_ICON} Tối đa ${Number(type.max_guests)} khách</li>
            ${type.area_sqm ? `<li class="chip chip-glass">${Number(type.area_sqm)} m²</li>` : ""}
            <li class="chip chip-glass">${Number(type.room_count)} phòng</li>
          </ul>
          <div class="room-name-row">
            <h3>${name}</h3>
            <p>từ <strong>${formatPrice(type.price_per_night)}</strong>/đêm</p>
          </div>
          <button class="room-detail-link" type="button" data-type-detail="${Number(type.id)}">Xem cấu trúc phòng</button>
        </div>
      </article>`;
  }).join("");
}

// Sau khi tìm theo ngày: hiện hạng phòng còn trống và số lượng, không hiện số phòng
function renderAvailability(types) {
  const resetLink = '<button type="button" class="link-button" data-show-types>Xem lại các hạng phòng</button>';
  roomsTitle.innerHTML = 'Phòng <em class="script">còn trống</em> cho bạn';
  if (!types.length) {
    roomGrid.innerHTML = `<p class="room-empty">Không còn phòng trống cho ngày và số khách bạn chọn. Thử đổi ngày hoặc giảm số khách. ${resetLink}</p>`;
    roomsStatus.textContent = "";
    return;
  }
  if (preferredTypeId) {
    types = [...types].sort((a, b) => (Number(b.id) === preferredTypeId) - (Number(a.id) === preferredTypeId));
  }
  roomsStatus.innerHTML = `${types.length} hạng phòng còn trống từ ${formatDateForSummary(checkin.value)} đến ${formatDateForSummary(checkout.value)}. Chọn hạng phòng để đặt, số phòng được xếp khi bạn nhận phòng. ${resetLink}`;
  const nights = Math.max(1, Math.round((new Date(checkout.value) - new Date(checkin.value)) / 86400000));
  roomGrid.innerHTML = types.map(type => {
    const name = escapeHtml(type.name);
    const left = Number(type.available_count);
    return `
      <article class="room-card is-bookable">
        <img src="${escapeHtml(type.image_url || FALLBACK_ROOM_IMAGE)}" alt="${name} tại Grandview" width="800" height="1000" loading="lazy">
        <button class="room-arrow" type="button" data-room-type-id="${Number(type.id)}" data-room-label="${name}"
                data-room-price="${Number(type.price_per_night)}" aria-label="Đặt ${name}">${ARROW_ICON}</button>
        <div class="room-overlay">
          <ul class="chip-row">
            <li class="chip chip-glass chip-strong">${left <= 2 ? `Chỉ còn ${left} phòng` : `Còn ${left} phòng`}</li>
            <li class="chip chip-glass">${PERSON_ICON} Tối đa ${Number(type.max_guests)} khách</li>
          </ul>
          <div class="room-name-row">
            <h3>${name}</h3>
            <p><strong>${formatPrice(type.price_per_night * nights)}</strong>/${nights} đêm</p>
          </div>
          <button class="room-detail-link" type="button" data-type-detail="${Number(type.id)}">Xem cấu trúc phòng</button>
        </div>
      </article>`;
  }).join("");
}

async function loadRooms() {
  try {
    renderRoomTypes(await apiRequest("/api/room-types"));
  } catch (error) {
    roomsStatus.textContent = "Chưa tải được danh sách hạng phòng. Đây là các hạng phòng tham khảo, hãy tải lại trang sau ít phút.";
  }
}

// Giá "từ ..." trong bảng dịch vụ lấy từ danh mục thật ở máy chủ
async function loadServicePrices() {
  try {
    const catalog = await apiRequest("/api/services/catalog");
    catalog.forEach(category => {
      const cell = document.querySelector(`[data-service-category="${category.id}"] [data-min-price]`);
      const prices = category.items.map(item => Number(item.price)).filter(price => price > 0);
      if (cell && prices.length) cell.innerHTML = `từ <strong>${formatPrice(Math.min(...prices))}</strong>`;
    });
  } catch {
    /* giữ giá mẫu trong HTML */
  }
}

document.querySelector("#booking").addEventListener("submit", async event => {
  event.preventDefault();
  if (!checkin.value || !checkout.value || checkout.value <= checkin.value) {
    notify("Ngày trả phòng phải sau ngày nhận phòng. Chọn lại ngày trả phòng.");
    return;
  }
  const params = new URLSearchParams({
    check_in: checkin.value,
    check_out: checkout.value,
    guests: String(guestCount()),
  });
  try {
    renderAvailability(await apiRequest(`/api/room-availability?${params.toString()}`));
    document.querySelector("#rooms").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    notify(`Chưa tìm được phòng trống. ${error.message}`);
  }
});

// ===== Cấu trúc hạng phòng =====
const roomTypeModal = document.querySelector("#roomTypeModal");
let detailType = null;

function galleryCaption(src) {
  if (/so-do/.test(src)) return "Sơ đồ bố trí phòng.";
  if (/\.svg($|\?)/.test(src)) return "Ảnh minh họa phòng, ảnh thật sẽ thay sau.";
  return "";
}

function showGalleryImage(index) {
  const sources = detailType.gallery.length ? detailType.gallery : [FALLBACK_ROOM_IMAGE];
  const src = sources[Math.min(index, sources.length - 1)];
  const picture = document.querySelector("#roomTypeImage");
  picture.classList.toggle("is-plan", /so-do/.test(src));
  picture.src = src;
  picture.alt = `${detailType.name}, hình ${index + 1}`;
  document.querySelector("#roomTypeCaption").textContent = galleryCaption(src);
  document.querySelectorAll("#roomTypeThumbs button").forEach((button, position) => {
    button.setAttribute("aria-pressed", String(position === index));
  });
}

function openRoomTypeDetail(typeId) {
  const type = roomTypes.find(item => Number(item.id) === Number(typeId));
  if (!type) return;
  const gallery = [...(type.gallery || [])];
  if (type.image_url && !gallery.includes(type.image_url)) gallery.unshift(type.image_url);
  if (!gallery.length) gallery.push(FALLBACK_ROOM_IMAGE);
  detailType = { ...type, gallery };
  document.querySelector("#roomTypeTitle").textContent = type.name;
  document.querySelector("#roomTypeLead").textContent = type.description || "";
  document.querySelector("#roomTypeThumbs").innerHTML = detailType.gallery.map((src, index) =>
    `<button type="button" data-gallery="${index}" aria-pressed="${index === 0}">
       <img src="${escapeHtml(src)}" alt="" width="120" height="90" loading="lazy">
     </button>`).join("");
  const rows = [
    ["Diện tích", type.area_sqm ? `${Number(type.area_sqm)} m²` : null],
    ["Giường", type.bed_type],
    ["Hướng nhìn", type.view_name],
    ["Bố trí", type.layout_summary],
    ["Sức chứa", `Tối đa ${Number(type.max_guests)} khách`],
    ["Giá", `từ ${formatPrice(type.price_per_night)}/đêm`],
  ].filter(([, value]) => value);
  document.querySelector("#roomTypeLayout").innerHTML = rows.map(([label, value]) =>
    `<dt>${label}</dt><dd>${escapeHtml(String(value))}</dd>`).join("");
  document.querySelector("#roomTypeAmenities").innerHTML = (type.amenities || [])
    .map(item => `<li class="chip chip-line">${escapeHtml(item)}</li>`).join("");
  showGalleryImage(0);
  roomTypeModal.hidden = false;
  document.querySelector("#roomTypeClose").focus();
}

function closeRoomTypeDetail() {
  roomTypeModal.hidden = true;
  detailType = null;
}

document.querySelector("#roomTypeClose").addEventListener("click", closeRoomTypeDetail);
roomTypeModal.addEventListener("click", event => {
  if (event.target === roomTypeModal) { closeRoomTypeDetail(); return; }
  const thumb = event.target.closest("[data-gallery]");
  if (thumb) showGalleryImage(Number(thumb.dataset.gallery));
});
document.querySelector("#roomTypePick").addEventListener("click", () => {
  const typeId = detailType ? Number(detailType.id) : 0;
  closeRoomTypeDetail();
  pickRoomType(typeId);
});

function pickRoomType(typeId) {
  preferredTypeId = typeId || null;
  const type = roomTypes.find(item => Number(item.id) === preferredTypeId);
  document.querySelector("#booking").scrollIntoView({ behavior: "smooth", block: "center" });
  checkin.focus({ preventScroll: true });
  notify(type
    ? `Chọn ngày rồi bấm Tìm phòng trống để xem phòng ${type.name} còn trống.`
    : "Chọn ngày nhận và trả phòng rồi bấm Tìm phòng trống để đặt.");
}

document.querySelector("#rooms").addEventListener("click", event => {
  if (event.target.closest("[data-show-types]")) {
    preferredTypeId = null;
    if (roomTypes.length) renderRoomTypes(roomTypes); else loadRooms();
  }
});

roomGrid.addEventListener("click", event => {
  if (event.target.closest("[data-show-types]")) return;
  const detailButton = event.target.closest("[data-type-detail]");
  if (detailButton) { openRoomTypeDetail(Number(detailButton.dataset.typeDetail)); return; }
  const card = event.target.closest(".room-card");
  const button = card && card.querySelector(".room-arrow");
  if (!button || !button.dataset.roomTypeId) {
    if (card) pickRoomType(Number(card.dataset.typeId || button?.dataset.pickType || 0));
    return;
  }
  if (!checkin.value || !checkout.value || checkout.value <= checkin.value) {
    notify("Vui lòng chọn ngày nhận và trả phòng trước.");
    document.querySelector("#booking").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  selectedRoom = { typeId: Number(button.dataset.roomTypeId), label: button.dataset.roomLabel };
  openGuestBooking();
});

function openStaffLogin() {
  authError.textContent = "";
  authModal.hidden = false;
  document.querySelector("#authEmail").focus();
}

function closeStaffLogin() {
  authModal.hidden = true;
  authError.textContent = "";
}

accountBtn.addEventListener("click", () => {
  if (sessionStorage.getItem("hotel_access_token")) {
    sessionStorage.removeItem("hotel_access_token");
    currentUser = null;
    updateAccountButton();
    notify("Đã đăng xuất tài khoản nhân viên.");
  } else {
    openStaffLogin();
  }
});

document.querySelector("#authClose").addEventListener("click", closeStaffLogin);
authModal.addEventListener("click", event => {
  if (event.target === authModal) closeStaffLogin();
});

authForm.addEventListener("submit", async event => {
  event.preventDefault();
  authError.textContent = "";
  authSubmit.disabled = true;
  authSubmit.textContent = "Đang xử lý...";
  const email = document.querySelector("#authEmail").value.trim();
  const password = document.querySelector("#authPassword").value;
  try {
    const response = await apiRequest("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    if (!["admin", "receptionist"].includes(response.user.role)) {
      throw new Error("Cổng đăng nhập này chỉ dành cho Lễ tân và Quản trị viên.");
    }
    sessionStorage.setItem("hotel_access_token", response.access_token);
    currentUser = response.user;
    closeStaffLogin();
    window.location.replace("/management.html");
  } catch (error) {
    authError.textContent = error.message;
  } finally {
    authSubmit.disabled = false;
    authSubmit.textContent = "Đăng nhập";
  }
});

function openGuestBooking() {
  const summary = `${selectedRoom.label}, ${formatDateForSummary(checkin.value)} đến ${formatDateForSummary(checkout.value)}, ${guestCount()} khách.`;
  document.querySelector("#guestBookingSummary").textContent = summary;
  guestBookingError.textContent = "";
  guestBookingModal.hidden = false;
  document.querySelector("#customerName").focus();
}

function closeGuestBooking() {
  guestBookingModal.hidden = true;
  guestBookingError.textContent = "";
}

function formatDateForSummary(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("vi-VN").format(new Date(`${value}T00:00:00`));
}

document.querySelector("#guestBookingClose").addEventListener("click", closeGuestBooking);
guestBookingModal.addEventListener("click", event => {
  if (event.target === guestBookingModal) closeGuestBooking();
});

async function submitGuestBooking(event) {
  event.preventDefault();
  if (!selectedRoom) {
    closeGuestBooking();
    notify("Vui lòng chọn phòng trước khi đặt.");
    return;
  }
  guestBookingError.textContent = "";
  guestBookingSubmit.disabled = true;
  guestBookingSubmit.textContent = "Đang gửi yêu cầu...";
  const payload = {
    room_type_id: selectedRoom.typeId,
    check_in: checkin.value,
    check_out: checkout.value,
    guests: guestCount(),
    customer_name: document.querySelector("#customerName").value.trim(),
    customer_phone: document.querySelector("#customerPhone").value.trim(),
    customer_email: document.querySelector("#customerEmail").value.trim(),
    note: document.querySelector("#bookingNote").value.trim() || null,
  };

  try {
    const booking = await apiRequest("/api/bookings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    closeGuestBooking();
    selectedRoom = null;
    guestBookingForm.reset();
    document.querySelector("#successBookingCode").textContent = booking.booking_code;
    document.querySelector("#successBookingTotal").textContent = `${booking.room_type}, tiền phòng dự kiến ${formatPrice(booking.total_price)}. Số phòng được xếp khi bạn nhận phòng.`;
    bookingSuccessModal.hidden = false;
    try {
      const params = new URLSearchParams({ check_in: checkin.value, check_out: checkout.value, guests: String(guestCount()) });
      renderAvailability(await apiRequest(`/api/room-availability?${params.toString()}`));
    } catch (refreshError) {
      console.info("Không thể làm mới danh sách phòng sau khi đặt:", refreshError.message);
    }
  } catch (error) {
    guestBookingError.textContent = error.message;
  } finally {
    guestBookingSubmit.disabled = false;
    guestBookingSubmit.textContent = "Gửi yêu cầu đặt phòng";
  }
}

guestBookingForm.addEventListener("submit", submitGuestBooking);

document.querySelector("#bookingSuccessClose").addEventListener("click", () => { bookingSuccessModal.hidden = true; });
document.querySelector("#bookingSuccessDone").addEventListener("click", () => { bookingSuccessModal.hidden = true; });
bookingSuccessModal.addEventListener("click", event => {
  if (event.target === bookingSuccessModal) bookingSuccessModal.hidden = true;
});
document.querySelector("#copyBookingCode").addEventListener("click", async () => {
  const code = document.querySelector("#successBookingCode").textContent;
  try {
    await navigator.clipboard.writeText(code);
    notify("Đã sao chép mã đặt phòng.");
  } catch {
    notify("Không thể sao chép tự động. Hãy chọn và lưu lại mã đặt phòng.");
  }
});


const bookingStatusLabels = {
  pending: "Chờ lễ tân xác nhận",
  confirmed: "Đã xác nhận",
  rejected: "Yêu cầu bị từ chối",
  cancelled: "Đã hủy",
  checked_in: "Đang lưu trú",
  checked_out: "Đã hoàn tất",
};

function formatBookingDate(value) {
  if (!value) return "—";
  const dateValue = new Date(`${value}T00:00:00`);
  if (Number.isNaN(dateValue.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" }).format(dateValue);
}

function lookupFootnote(status) {
  return {
    pending: "Yêu cầu đang chờ lễ tân kiểm tra và xác nhận.",
    confirmed: "Đặt phòng đã được xác nhận. Hẹn gặp bạn tại Grandview.",
    checked_in: "Bạn đang lưu trú. Tiền dịch vụ được cộng khi lễ tân tiếp nhận yêu cầu.",
    checked_out: "Lượt lưu trú đã hoàn tất. Cảm ơn bạn đã nghỉ tại Grandview.",
    rejected: "Vui lòng liên hệ khách sạn nếu cần được hỗ trợ thêm.",
    cancelled: "Đơn này đã được hủy.",
  }[status] || "";
}

function renderBookingCard(booking) {
  const status = String(booking.status || "").toLowerCase();
  const safeStatus = Object.hasOwn(bookingStatusLabels, status) ? status : "unknown";
  const services = Number(booking.service_total || 0);
  return `
    <article class="lookup-booking-card ${safeStatus}">
      <div class="lookup-result-heading">
        <div>
          <small>Mã đặt phòng</small>
          <h3 class="lookup-code">${escapeHtml(booking.booking_code)}</h3>
        </div>
        <span class="status-badge ${safeStatus}">${escapeHtml(bookingStatusLabels[status] || "Đang cập nhật")}</span>
      </div>
      <button type="button" class="lookup-copy" data-copy-code="${escapeHtml(booking.booking_code)}">Sao chép mã</button>
      <dl class="lookup-detail-grid">
        <div><dt>Phòng</dt><dd>${booking.room_number ? `Phòng ${escapeHtml(booking.room_number)}, ` : ""}${escapeHtml(booking.room_type)}${booking.room_number ? "" : '<small class="lookup-hint">Số phòng được xếp khi nhận phòng</small>'}</dd></div>
        <div><dt>Lưu trú</dt><dd>${formatBookingDate(booking.check_in)} đến ${formatBookingDate(booking.check_out)}, ${Number(booking.nights)} đêm</dd></div>
        <div><dt>Người đặt</dt><dd>${escapeHtml(booking.customer_name)}</dd></div>
        <div><dt>Số khách</dt><dd>${Number(booking.guests)} người</dd></div>
      </dl>
      <dl class="lookup-money">
        <dt>Tiền phòng</dt><dd>${formatPrice(booking.room_total)}</dd>
        <dt>Dịch vụ phòng</dt><dd>${services ? formatPrice(services) : "Chưa dùng"}</dd>
        <dt class="lookup-money-total">Tổng cộng</dt><dd class="lookup-money-total">${formatPrice(booking.grand_total)}</dd>
        ${Number(booking.deposit_paid) ? `
          <dt>Đã đặt cọc</dt><dd>− ${formatPrice(booking.deposit_paid)}</dd>
          <dt class="lookup-money-due">Còn phải thanh toán</dt><dd class="lookup-money-due">${formatPrice(booking.amount_due)}</dd>` : ""}
      </dl>
      ${status === "checked_in"
        ? `<button class="gold-btn lookup-service-btn" type="button" data-open-room-service="${escapeHtml(booking.booking_code)}">Mở dịch vụ phòng</button>`
        : ""}
      <p class="lookup-result-footnote">${escapeHtml(lookupFootnote(status))}</p>
    </article>`;
}

function renderBookingLookup(bookings) {
  bookingLookupResult.innerHTML = `
    <p class="lookup-count">${bookings.length === 1 ? "Tìm thấy 1 đơn đặt phòng." : `Tìm thấy ${bookings.length} đơn đặt phòng, đơn đang diễn ra ở trên cùng.`}</p>
    ${bookings.map(renderBookingCard).join("")}`;
  bookingLookupResult.hidden = false;
}

bookingLookupForm.addEventListener("submit", async event => {
  event.preventDefault();
  bookingLookupError.textContent = "";
  bookingLookupResult.hidden = true;
  bookingLookupResult.innerHTML = "";

  const phone = bookingLookupPhone.value.trim();
  if ((phone.match(/\d/g) || []).length < 8) {
    bookingLookupError.textContent = "Vui lòng nhập số điện thoại hợp lệ.";
    return;
  }

  bookingLookupSubmit.disabled = true;
  bookingLookupSubmit.textContent = "Đang tra cứu...";
  try {
    const bookings = await apiRequest("/api/bookings/lookup-by-phone", {
      method: "POST",
      body: JSON.stringify({ phone }),
    });
    renderBookingLookup(bookings);
    bookingLookupResult.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    bookingLookupError.textContent = error.message;
  } finally {
    bookingLookupSubmit.disabled = false;
    bookingLookupSubmit.textContent = "Tra cứu đơn đặt phòng";
  }
});

bookingLookupResult.addEventListener("click", async event => {
  const copy = event.target.closest("[data-copy-code]");
  if (!copy) return;
  try {
    await navigator.clipboard.writeText(copy.dataset.copyCode);
    notify("Đã sao chép mã đặt phòng.");
  } catch {
    notify("Không thể sao chép tự động. Hãy chọn và lưu lại mã đặt phòng.");
  }
});


// ===== Dịch vụ phòng: khách nhập số điện thoại người đặt phòng =====
const roomServiceForm = document.querySelector("#roomServiceForm");
const roomServiceError = document.querySelector("#roomServiceError");
const roomServiceSubmit = document.querySelector("#roomServiceSubmit");
const roomServiceChoices = document.querySelector("#roomServiceChoices");
const roomServiceChoiceList = document.querySelector("#roomServiceChoiceList");
const GUEST_TOKEN_KEY = "gv_guest_token";
let pendingStays = [];

function openRoomServiceStay(stay) {
  sessionStorage.setItem(GUEST_TOKEN_KEY, stay.token);
  window.location.href = "/dich-vu.html";
}

async function enterRoomService(credentials) {
  const result = await apiRequest("/api/guest/service-session", {
    method: "POST",
    body: JSON.stringify(credentials),
  });
  if (result.stays.length === 1) {
    openRoomServiceStay(result.stays[0]);
    return;
  }
  pendingStays = result.stays;
  roomServiceChoiceList.innerHTML = result.stays.map((stay, index) => `
    <button type="button" class="secondary-button" data-stay-index="${index}">
      Phòng ${escapeHtml(stay.room_number)}<small>${escapeHtml(stay.room_type)}</small>
    </button>`).join("");
  roomServiceChoices.hidden = false;
}

roomServiceForm.addEventListener("submit", async event => {
  event.preventDefault();
  roomServiceError.textContent = "";
  roomServiceChoices.hidden = true;
  const phone = document.querySelector("#roomServicePhone").value.trim();
  if ((phone.match(/\d/g) || []).length < 8) {
    roomServiceError.textContent = "Vui lòng nhập số điện thoại hợp lệ.";
    return;
  }
  roomServiceSubmit.disabled = true;
  roomServiceSubmit.textContent = "Đang kiểm tra...";
  try {
    await enterRoomService({ phone });
  } catch (error) {
    roomServiceError.textContent = error.message;
    // Khách đã đặt nhưng chưa nhận phòng: gợi ý xem trước dịch vụ thay vì để ngõ cụt
    if (error.status === 403) {
      roomServiceError.append(" ");
      const link = document.createElement("a");
      link.href = "/dich-vu.html";
      link.textContent = "Xem trước dịch vụ và bảng giá";
      roomServiceError.append(link);
    }
  } finally {
    roomServiceSubmit.disabled = false;
    roomServiceSubmit.textContent = "Vào dịch vụ phòng";
  }
});

roomServiceChoiceList.addEventListener("click", event => {
  const button = event.target.closest("[data-stay-index]");
  if (button) openRoomServiceStay(pendingStays[Number(button.dataset.stayIndex)]);
});

// Nút "Mở dịch vụ phòng" trong kết quả tra cứu đơn dùng mã đặt phòng
bookingLookupResult.addEventListener("click", async event => {
  const button = event.target.closest("[data-open-room-service]");
  if (!button) return;
  try {
    await enterRoomService({ booking_code: button.dataset.openRoomService });
  } catch (error) {
    notify(error.message);
  }
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    if (!roomTypeModal.hidden) closeRoomTypeDetail();
    if (!authModal.hidden) closeStaffLogin();
    if (!guestBookingModal.hidden) closeGuestBooking();
    if (!bookingSuccessModal.hidden) bookingSuccessModal.hidden = true;
  }
});

function updateAccountButton() {
  accountBtn.textContent = currentUser ? "Mở quản lý" : "Đăng nhập nhân viên";
  accountBtn.title = currentUser ? `${currentUser.full_name} (${currentUser.role})` : "Đăng nhập cho nhân viên";
}

async function restoreSession() {
  if (!sessionStorage.getItem("hotel_access_token")) return;
  try {
    currentUser = await apiRequest("/api/auth/me");
    if (["admin", "receptionist"].includes(currentUser.role)) {
      window.location.replace("/management.html");
      return;
    }
    sessionStorage.removeItem("hotel_access_token");
    currentUser = null;
  } catch {
    sessionStorage.removeItem("hotel_access_token");
    currentUser = null;
  }
  updateAccountButton();
}

updateAccountButton();
restoreSession().finally(() => { loadRooms(); loadServicePrices(); });
