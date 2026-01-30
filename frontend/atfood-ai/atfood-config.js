const isLocal =
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1";

window.ATFOOD_BASE_PATH = isLocal ? "" : "/atfoodai";
window.ATFOOD_API_URL = isLocal
  ? "http://127.0.0.1:8000/api/atfood"
  : `${window.ATFOOD_BASE_PATH}/api/atfood`;
