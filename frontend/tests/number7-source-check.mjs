import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");

const pageSource = fs.readFileSync(
  path.join(projectRoot, "src", "pages", "WorkspacePage.jsx"),
  "utf8",
);
const stylesSource = fs.readFileSync(
  path.join(projectRoot, "src", "styles.css"),
  "utf8",
);

assert.match(
  pageSource,
  /<table[\s>]/,
  "WorkspacePage.jsx должен рендерить таблицу результатов вместо карточечной стопки.",
);

assert.match(
  pageSource,
  /Новый поиск/,
  "В шапке результатов должна быть кнопка «Новый поиск».",
);

assert.match(
  pageSource,
  /expandedSlugs/,
  "Для metro_breakdown нужен локальный state expandedSlugs.",
);

assert.match(
  stylesSource,
  /\.workspace-results-table\b/,
  "styles.css должен содержать стили для табличной зоны workspace-results-table.",
);

assert.match(
  stylesSource,
  /\.workspace-results-table-shell\b/,
  "styles.css должен содержать обёртку таблицы с горизонтальным скроллом.",
);

console.log("number7 source check passed");
