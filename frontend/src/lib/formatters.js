export function formatPopulation(value) {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)} млн`;
  }

  return `${value} тыс`;
}

export function formatAverage(value) {
  if (typeof value !== "number") {
    return "—";
  }

  return value.toFixed(1);
}

export function formatTopViews(value) {
  if (typeof value !== "number") {
    return "?";
  }

  return `${value} просм`;
}
