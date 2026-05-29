import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchBootstrap, fetchResults, fetchStatus, startSearch } from "../lib/api";
import { formatAverage, formatPopulation, formatTopViews } from "../lib/formatters";

function buildInitialForm(bootstrap) {
  return {
    query: "",
    count: bootstrap?.defaults?.count ?? 150,
    priceMin: "",
    priceMax: "",
    cities: bootstrap?.defaults?.selected_city_slugs ?? [],
    gender: [],
  };
}

function normalizePrice(value) {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return null;
  }
  return parsed;
}

function describeFormFilters(form) {
  const parts = [];
  const priceMin = normalizePrice(form.priceMin);
  const priceMax = normalizePrice(form.priceMax);
  const genderValue = form.gender.length === 1 ? form.gender[0] : null;

  if (priceMin !== null && priceMax !== null) {
    parts.push(`от ${priceMin} ₽ до ${priceMax} ₽`);
  } else if (priceMin !== null) {
    parts.push(`от ${priceMin} ₽`);
  } else if (priceMax !== null) {
    parts.push(`до ${priceMax} ₽`);
  }

  if (genderValue === "male") {
    parts.push("мужское");
  } else if (genderValue === "female") {
    parts.push("женское");
  }

  return parts.join(", ");
}

function buildActiveSearch(form) {
  return {
    query: form.query.trim(),
    price_desc: describeFormFilters(form),
    cities_count: form.cities.length,
    count: Number(form.count) || 0,
  };
}

function buildQuerySummary(data) {
  if (!data) {
    return "";
  }

  return `${data.query} · ${data.price_desc || "без фильтров"} · городов: ${data.cities_count} · по ${data.count} объявлений`;
}

function getWorkspaceMode({ bootstrap, error, status, results }) {
  if (!bootstrap && !error) {
    return "loading";
  }

  if (results?.status === "done") {
    return "results";
  }

  if (status?.status === "running") {
    return "running";
  }

  return "form";
}

function hasMetroBreakdown(city) {
  return Array.isArray(city.metro_breakdown) && city.metro_breakdown.length > 0;
}

function WorkspaceStatePanel({ kicker, title, copy, tone = "default" }) {
  const className =
    tone === "error"
      ? "error-panel workspace-state-panel"
      : "workspace-panel workspace-state-panel";

  return (
    <section className={className}>
      <p className="section-kicker">{kicker}</p>
      <h2 className="workspace-panel-title">{title}</h2>
      <p className="workspace-body-copy">{copy}</p>
    </section>
  );
}

function WorkspaceLoadingSkeleton() {
  return (
    <section className="workspace-panel workspace-skeleton-panel" aria-label="Загрузка формы">
      <div>
        <p className="section-kicker">Workspace</p>
        <h2 className="workspace-panel-title">Загружаем рабочий экран</h2>
        <p className="workspace-body-copy">
          Подтягиваем города и дефолтные настройки, чтобы форма открылась сразу в рабочем
          состоянии.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.45fr)_170px_190px]">
        <div className="workspace-skeleton-field" aria-hidden="true">
          <div className="workspace-skeleton-label" />
          <div className="workspace-skeleton-control" />
        </div>
        <div className="workspace-skeleton-field" aria-hidden="true">
          <div className="workspace-skeleton-label workspace-skeleton-label-sm" />
          <div className="workspace-skeleton-control" />
        </div>
        <div className="workspace-skeleton-field workspace-skeleton-field-cta" aria-hidden="true">
          <div className="workspace-skeleton-pill" />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={`filter-${index}`} className="workspace-skeleton-field" aria-hidden="true">
            <div className="workspace-skeleton-label workspace-skeleton-label-sm" />
            <div className="workspace-skeleton-control" />
          </div>
        ))}
      </div>

      <div className="workspace-skeleton-field" aria-hidden="true">
        <div className="workspace-skeleton-label workspace-skeleton-label-md" />
        <div className="workspace-skeleton-chip-grid">
          {Array.from({ length: 10 }).map((_, index) => (
            <div key={`city-${index}`} className="workspace-skeleton-chip" />
          ))}
        </div>
      </div>
    </section>
  );
}

function SearchForm({
  bootstrap,
  form,
  onChange,
  onToggleCity,
  onSetCities,
  onToggleGender,
  onSubmit,
  isSubmitting,
}) {
  return (
    <form className="workspace-panel space-y-5" onSubmit={onSubmit}>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.45fr)_170px_190px]">
        <label className="field-shell">
          <span>Что исследуем</span>
          <input
            className="field-input"
            type="text"
            name="query"
            value={form.query}
            onChange={onChange}
            placeholder="Например: диван угловой"
            required
          />
        </label>

        <label className="field-shell">
          <span>Объявлений на город</span>
          <input
            className="field-input"
            type="number"
            min="1"
            max="500"
            name="count"
            value={form.count}
            onChange={onChange}
          />
        </label>

        <button className="submit-button" type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Запускаем..." : "Запустить анализ"}
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <label className="field-shell">
          <span>Цена от</span>
          <input
            className="field-input"
            type="number"
            min="0"
            name="priceMin"
            value={form.priceMin}
            onChange={onChange}
            placeholder="0"
          />
        </label>

        <label className="field-shell">
          <span>Цена до</span>
          <input
            className="field-input"
            type="number"
            min="0"
            name="priceMax"
            value={form.priceMax}
            onChange={onChange}
            placeholder="0"
          />
        </label>

        <div className="field-shell">
          <span>Пол</span>
          <div className="mt-3 flex gap-3">
            {[
              { value: "male", label: "Мужское" },
              { value: "female", label: "Женское" },
            ].map((option) => (
              <label key={option.value} className="chip-checkbox">
                <input
                  type="checkbox"
                  checked={form.gender.includes(option.value)}
                  onChange={() => onToggleGender(option.value)}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="field-shell">
          <span>Быстрые наборы</span>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="mini-action" onClick={() => onSetCities("top10")}>
              Топ-10
            </button>
            <button type="button" className="mini-action" onClick={() => onSetCities("all")}>
              Все
            </button>
            <button type="button" className="mini-action" onClick={() => onSetCities("none")}>
              Очистить
            </button>
          </div>
        </div>
      </div>

      <div className="field-shell">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>Города</span>
          <p className="workspace-selection-summary">
            Выбрано: <strong className="workspace-selection-value">{form.cities.length}</strong>
          </p>
        </div>

        <div className="city-grid mt-4">
          {bootstrap.cities.map((city) => {
            const checked = form.cities.includes(city.slug);

            return (
              <button
                key={city.slug}
                type="button"
                className={`city-chip ${checked ? "city-chip-active" : ""}`}
                onClick={() => onToggleCity(city.slug)}
              >
                <span>{city.name}</span>
                <span>{formatPopulation(city.population)}</span>
              </button>
            );
          })}
        </div>
      </div>
    </form>
  );
}

function ProgressPanel({ status, onReset }) {
  const percent =
    status.total > 0 ? Math.min(100, Math.round((status.done / status.total) * 100)) : 0;

  return (
    <section className="workspace-panel">
      <div className="workspace-panel-header">
        <div>
          <p className="section-kicker">Background job</p>
          <h2 className="workspace-panel-title">Парсинг в работе</h2>
          <p className="workspace-body-copy">
            Сервис обходит выбранные города и обновляет прогресс по мере завершения каждой группы
            объявлений. Фоновую задачу не отменяем: кнопка ниже просто возвращает вас к форме
            нового поиска.
          </p>
        </div>

        <div className="workspace-progress-side">
          <div className="workspace-stage-card">
            <p className="workspace-stage-label">Stage</p>
            <p className="workspace-stage-value">{status.current || "Подготовка среды"}</p>
          </div>

          <button type="button" className="secondary-button" onClick={onReset}>
            Новый поиск
          </button>
        </div>
      </div>

      <div className="workspace-progress-shell">
        <div className="progress-bar-fill" style={{ width: `${percent}%` }} />
      </div>

      <div className="workspace-progress-meta">
        <span className="workspace-progress-value">
          {status.done} / {status.total}
        </span>
        <span className="workspace-progress-value">{percent}%</span>
      </div>
    </section>
  );
}

function ResultTopCell({ item }) {
  if (!item) {
    return <span className="workspace-top-empty">—</span>;
  }

  const meta = item.address || item.metro || "—";
  const viewsLabel = formatTopViews(item.views_today);

  return (
    <div className="workspace-top-entry">
      {item.url ? (
        <a href={item.url} target="_blank" rel="noreferrer" className="workspace-top-link">
          {viewsLabel}
        </a>
      ) : (
        <span className="workspace-top-value">{viewsLabel}</span>
      )}
      <span className="workspace-top-meta" title={meta}>
        {meta}
      </span>
    </div>
  );
}

function ResultsTable({ data, expandedSlugs, onToggleExpand, onReset }) {
  const isEmpty = !data.results?.length;

  return (
    <section className="space-y-5">
      <div className="workspace-panel workspace-panel-header">
        <div>
          <p className="section-kicker">Results</p>
          <h2 className="workspace-panel-title">{data.query}</h2>
        </div>

        <div className="workspace-results-actions">
          <button type="button" className="secondary-button" onClick={onReset}>
            Новый поиск
          </button>
          <a href={data.export_url} className="primary-link-button">
            Скачать CSV
          </a>
        </div>
      </div>

      {isEmpty ? (
        <WorkspaceStatePanel
          kicker="Empty result"
          title="Подходящих данных пока нет"
          copy="Сервис завершил задачу, но после фильтра локальности и свежести не осталось объявлений, из которых можно честно собрать метрику. Попробуйте расширить запрос, увеличить лимит или выбрать другой набор городов."
        />
      ) : (
        <div className="workspace-panel workspace-results-table-shell">
          <div className="workspace-results-scroll">
            <table className="workspace-results-table">
              <thead>
                <tr>
                  <th scope="col" className="workspace-table-sticky workspace-city-column">
                    Город
                  </th>
                  <th scope="col">Местных</th>
                  <th scope="col">Среднее/день</th>
                  <th scope="col">Топ-1</th>
                  <th scope="col">Топ-2</th>
                  <th scope="col">Топ-3</th>
                </tr>
              </thead>
              <tbody>
                {data.results.map((city) => {
                  const isExpanded = expandedSlugs.has(city.city_slug);
                  const cityHasMetro = hasMetroBreakdown(city);

                  return (
                    <Fragment key={city.city_slug}>
                      <tr className="workspace-results-row">
                        <th
                          scope="row"
                          className="workspace-table-sticky workspace-city-cell"
                        >
                          <div className="workspace-city-stack">
                            <span className="workspace-city-name">{city.city_name}</span>
                            {cityHasMetro ? (
                              <button
                                type="button"
                                className="workspace-metro-toggle"
                                onClick={() => onToggleExpand(city.city_slug)}
                                aria-expanded={isExpanded}
                              >
                                {isExpanded ? "м. ▴" : "м. ▾"}
                              </button>
                            ) : null}
                          </div>
                        </th>
                        <td className="workspace-count-cell">{city.local_count ?? 0}</td>
                        <td className="workspace-average-cell">
                          {formatAverage(city.avg_views_today)}
                        </td>
                        {[0, 1, 2].map((index) => (
                          <td key={`${city.city_slug}-top-${index}`}>
                            <ResultTopCell item={city.top3?.[index] ?? null} />
                          </td>
                        ))}
                      </tr>

                      {cityHasMetro && isExpanded
                        ? city.metro_breakdown.map((station) => (
                            <tr
                              key={`${city.city_slug}-${station.metro}`}
                              className="workspace-results-row workspace-results-row-metro"
                            >
                              <th
                                scope="row"
                                className="workspace-table-sticky workspace-city-cell workspace-city-cell-metro"
                              >
                                <span className="workspace-metro-name">м. {station.metro}</span>
                              </th>
                              <td className="workspace-count-cell workspace-cell-empty">—</td>
                              <td className="workspace-average-cell">
                                {formatAverage(station.avg_views_today)}
                              </td>
                              {[0, 1, 2].map((index) => (
                                <td key={`${city.city_slug}-${station.metro}-top-${index}`}>
                                  <ResultTopCell item={station.top3?.[index] ?? null} />
                                </td>
                              ))}
                            </tr>
                          ))
                        : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

export default function WorkspacePage() {
  const [bootstrap, setBootstrap] = useState(null);
  const [form, setForm] = useState(buildInitialForm(null));
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [activeSearch, setActiveSearch] = useState(null);
  const [expandedSlugs, setExpandedSlugs] = useState(() => new Set());
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    fetchBootstrap()
      .then((data) => {
        if (cancelled) {
          return;
        }

        setBootstrap(data);
        setForm(buildInitialForm(data));
      })
      .catch((fetchError) => {
        if (cancelled) {
          return;
        }

        setError(fetchError.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!jobId || !status || status.status !== "running") {
      return undefined;
    }

    const intervalId = window.setInterval(async () => {
      try {
        const nextStatus = await fetchStatus(jobId);

        if (nextStatus.status === "done") {
          const readyResults = await fetchResults(jobId);
          setResults(readyResults);
          setStatus(readyResults);
          window.clearInterval(intervalId);
          return;
        }

        setStatus(nextStatus);

        if (nextStatus.status === "blocked") {
          setError(nextStatus.error || "Avito ограничил доступ — попробуйте позже.");
          window.clearInterval(intervalId);
        }

        if (nextStatus.status === "error") {
          setError(nextStatus.error || "Не удалось завершить задачу.");
          window.clearInterval(intervalId);
        }
      } catch (pollError) {
        setError(pollError.message);
        window.clearInterval(intervalId);
      }
    }, 2000);

    return () => window.clearInterval(intervalId);
  }, [jobId, status]);

  function updateField(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  function toggleCity(slug) {
    setForm((current) => {
      const exists = current.cities.includes(slug);
      return {
        ...current,
        cities: exists
          ? current.cities.filter((item) => item !== slug)
          : [...current.cities, slug],
      };
    });
  }

  function setCities(mode) {
    if (!bootstrap) {
      return;
    }

    if (mode === "all") {
      setForm((current) => ({
        ...current,
        cities: bootstrap.cities.map((city) => city.slug),
      }));
      return;
    }

    if (mode === "top10") {
      setForm((current) => ({
        ...current,
        cities: bootstrap.defaults.selected_city_slugs,
      }));
      return;
    }

    setForm((current) => ({ ...current, cities: [] }));
  }

  function toggleGender(value) {
    setForm((current) => {
      const exists = current.gender.includes(value);
      return {
        ...current,
        gender: exists
          ? current.gender.filter((item) => item !== value)
          : [...current.gender, value],
      };
    });
  }

  function toggleExpanded(slug) {
    setExpandedSlugs((current) => {
      const next = new Set(current);
      if (next.has(slug)) {
        next.delete(slug);
      } else {
        next.add(slug);
      }
      return next;
    });
  }

  function resetSearchView() {
    setResults(null);
    setStatus(null);
    setJobId("");
    setError("");
    setActiveSearch(null);
    setExpandedSlugs(new Set());
  }

  async function handleSubmit(event) {
    event.preventDefault();

    if (!form.cities.length) {
      setError("Выберите хотя бы один город, иначе сервису нечего анализировать.");
      return;
    }

    setError("");
    setResults(null);
    setStatus(null);
    setJobId("");
    setExpandedSlugs(new Set());
    setActiveSearch(buildActiveSearch(form));
    setIsSubmitting(true);

    try {
      const payload = {
        query: form.query,
        count: Number(form.count),
        price_min: form.priceMin,
        price_max: form.priceMax,
        cities: form.cities,
        gender: form.gender,
      };
      const started = await startSearch(payload);

      setJobId(started.job_id);

      if (started.status === "done") {
        const readyResults = await fetchResults(started.job_id);
        setResults(readyResults);
        setStatus(readyResults);
        return;
      }

      setStatus(started);

      if (started.status === "blocked") {
        setError(started.error || "Avito ограничил доступ — попробуйте позже.");
      }

      if (started.status === "error") {
        setError(started.error || "Не удалось завершить задачу.");
      }
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  const workspaceMode = getWorkspaceMode({ bootstrap, error, status, results });
  const summarySource = workspaceMode === "results" ? results : activeSearch;

  return (
    <main className="workspace-page">
      <div className="page-noise" />

      <div className="page-container workspace-page-shell">
        <header className="workspace-page-header">
          <nav className="topbar" aria-label="Навигация рабочего экрана">
            <span className="topbar-wordmark">AVITO RESEARCH</span>
            <Link to="/" className="secondary-button">
              Назад к кейсу
            </Link>
          </nav>

          <div className="workspace-header-shell">
            <div className="workspace-header-copy">
              <p className="section-kicker">Workspace</p>
              <h1 className="workspace-title">Рабочий экран аналитики</h1>
              {workspaceMode === "running" || workspaceMode === "results" ? (
                <p className="workspace-summary-line">{buildQuerySummary(summarySource)}</p>
              ) : (
                <p className="workspace-intro-copy">
                  Здесь живёт рабочая часть продукта: тот же backend-процесс, реальные задачи,
                  прогресс, результаты и CSV, но уже в спокойной оболочке той же системы.
                </p>
              )}
            </div>
          </div>
        </header>

        <section className="workspace-main-stack" aria-label="Рабочая форма и результаты">
          {error ? (
            <WorkspaceStatePanel
              kicker="Error state"
              title="Не удалось продолжить запрос"
              copy={error}
              tone="error"
            />
          ) : null}

          {workspaceMode === "loading" ? <WorkspaceLoadingSkeleton /> : null}

          {workspaceMode === "form" && bootstrap ? (
            <SearchForm
              bootstrap={bootstrap}
              form={form}
              onChange={updateField}
              onToggleCity={toggleCity}
              onSetCities={setCities}
              onToggleGender={toggleGender}
              onSubmit={handleSubmit}
              isSubmitting={isSubmitting}
            />
          ) : null}

          {workspaceMode === "running" && status ? (
            <ProgressPanel status={status} onReset={resetSearchView} />
          ) : null}

          {workspaceMode === "results" && results ? (
            <ResultsTable
              data={results}
              expandedSlugs={expandedSlugs}
              onToggleExpand={toggleExpanded}
              onReset={resetSearchView}
            />
          ) : null}
        </section>

        <footer className="site-footer workspace-footer" role="contentinfo">
          <p className="footer-left">© 2026 Avito Research</p>
          <nav className="footer-right" aria-label="Навигация футера">
            <Link to="/" className="footer-link">
              К кейсу
            </Link>
          </nav>
        </footer>
      </div>
    </main>
  );
}
