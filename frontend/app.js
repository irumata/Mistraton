const message = document.querySelector("#message");
const refresh = document.querySelector("#refresh");

async function loadMessage() {
  refresh.disabled = true;
  message.textContent = "Connecting to the API…";
  try {
    const base = new URL(window.API_BASE_URL);
    if (!["http:", "https:"].includes(base.protocol)) {
      throw new Error("Invalid API URL");
    }
    const response = await fetch(new URL("/api/message", base), {
      signal: AbortSignal.timeout(10000),
      credentials: "omit",
    });
    if (!response.ok) throw new Error("API request failed");
    const data = await response.json();
    if (typeof data.message !== "string") throw new Error("Invalid API response");
    message.textContent = data.message;
  } catch {
    message.textContent =
      "Cannot reach the backend. Check the API URL and try again.";
  } finally {
    refresh.disabled = false;
  }
}

refresh.addEventListener("click", loadMessage);
loadMessage();
