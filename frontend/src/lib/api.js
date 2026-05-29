async function readJson(response) {
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const message = data.error || `HTTP ${response.status}`;
    throw new Error(message);
  }

  return data;
}

export async function fetchBootstrap() {
  const response = await fetch("/api/bootstrap");
  return readJson(response);
}

export async function startSearch(payload) {
  const response = await fetch("/api/search", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  return readJson(response);
}

export async function fetchStatus(jobId) {
  const response = await fetch(`/api/status/${jobId}`);
  return readJson(response);
}

export async function fetchResults(jobId) {
  const response = await fetch(`/api/results/${jobId}`);
  return readJson(response);
}
