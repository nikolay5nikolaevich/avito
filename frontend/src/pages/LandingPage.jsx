import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { formatPopulation } from "../lib/formatters";
import HeroSculpture from "../components/HeroSculpture";

// ─── Данные ────────────────────────────────────────────────────────────────

const storyFeatures = [
  {
    title: "Фильтр локальности",
    desc: "В расчёт идут только объявления этого города. Чужие регионы вычищаются до расчёта.",
  },
  {
    title: "Просмотры в день",
    desc: "Живая дневная динамика вместо накопленной витрины.",
  },
  {
    title: "Разбивка по метро",
    desc: "Для Москвы и Санкт-Петербурга: отдельная аналитика по каждой станции.",
  },
  {
    title: "Кэш и экспорт",
    desc: "Результаты сохраняются на 24 часа. Выгрузка в CSV одним кликом.",
  },
];

const indexMetrics = [
  {
    idx: "01",
    label: "Сценарий",
    value: "10-50 городов",
    copy: "Локальное сравнение без ручной разметки объявлений.",
  },
  {
    idx: "02",
    label: "Сигнал",
    value: "Views / day",
    copy: "Фокус на живой дневной динамике, а не на накопленных цифрах.",
  },
  {
    idx: "03",
    label: "Точность",
    value: "Local filter",
    copy: "Чужие регионы вычищаются до расчёта метрик.",
  },
];

const teaserCities = [
  { name: "Москва", population: 13149 },
  { name: "Санкт-Петербург", population: 5598 },
  { name: "Новосибирск", population: 1634 },
  { name: "Екатеринбург", population: 1539 },
];

const teaserLines = [
  { id: "line-1", width: "100%" },
  { id: "line-2", width: "91%" },
  { id: "line-3", width: "85%" },
  { id: "line-4", width: "79%" },
];

// ─── Хук: IntersectionObserver scroll-reveal ───────────────────────────────

function useReveal(rootMargin = "0px 0px -60px 0px") {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Уважаем prefers-reduced-motion: сразу показываем без анимации
    const prefersReduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;
    if (prefersReduced) {
      el.querySelectorAll(".reveal-target").forEach((node) => {
        node.classList.add("is-revealed");
      });
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-revealed");
            observer.unobserve(entry.target);
          }
        });
      },
      { rootMargin }
    );

    el.querySelectorAll(".reveal-target").forEach((node) => {
      observer.observe(node);
    });

    return () => observer.disconnect();
  }, [rootMargin]);

  return ref;
}

// ─── Teaser-заглушка сервиса ───────────────────────────────────────────────

function ServiceTeaser() {
  return (
    <div className="teaser-shell">
      {/* Строка поиска + выбор количества + ценовой диапазон */}
      <div className="teaser-toolbar">
        <div className="teaser-input" style={{ flex: "3 1 0" }} />
        <div className="teaser-input" style={{ flex: "1 1 0", minWidth: "5rem" }} />
        <div className="teaser-input" style={{ flex: "1.2 1 0", minWidth: "6rem" }} />
      </div>

      {/* Фильтры */}
      <div className="teaser-filters-row">
        <div className="teaser-filter" />
        <div className="teaser-filter" />
        <div className="teaser-filter" />
      </div>

      {/* Города-пилюли */}
      <div className="teaser-cities">
        {teaserCities.map((city) => (
          <div key={city.name} className="teaser-city-pill">
            <span>{city.name}</span>
            <span>{formatPopulation(city.population)}</span>
          </div>
        ))}
      </div>

      {/* Строки результатов */}
      <div className="teaser-results">
        {teaserLines.map((line) => (
          <div key={line.id} className="teaser-line" style={{ width: line.width }} />
        ))}
      </div>
    </div>
  );
}

// ─── Главная страница ──────────────────────────────────────────────────────

export default function LandingPage() {
  const heroRef = useReveal("0px");
  const indexRef = useReveal();
  const storyRef = useReveal();
  const chamberRef = useReveal();
  const teaserRef = useReveal();
  const authorRef = useReveal();

  return (
    <main className="min-h-[100dvh]" style={{ backgroundColor: "var(--ink-900)" }}>
      {/* Нейтральный зерновой оверлей (без синих пятен) */}
      <div className="page-noise" />

      <div className="page-container">

        {/* ── 1. Topbar ────────────────────────────────────────────────────── */}
        <header>
          <nav className="topbar" aria-label="Главная навигация">
            <span className="topbar-wordmark">Avito Research</span>
            <Link to="/workspace" className="btn-primary">
              Open workspace
            </Link>
          </nav>
        </header>

        {/* ── 2. Hero ──────────────────────────────────────────────────────── */}
        <section
          className="hero-section"
          aria-label="Hero"
          ref={heroRef}
        >
          <div className="hero-inner">
            {/* Левая колонка: текст + CTA */}
            <div className="hero-copy">
              <p className="hero-kicker reveal-target">
                Аналитика локального спроса
              </p>

              <h1 className="hero-title display-xl reveal-target" data-delay="1">
                Ручной просмотр Avito не покажет, где спрос реальный.
              </h1>

              <p className="hero-body reveal-target" data-delay="2">
                Сервис проходит по выбранным городам, берёт только местные
                объявления и считает просмотры в день. Так видно, где спрос
                настоящий, а где выдачу разбавили чужие регионы.
              </p>

              <div className="hero-actions reveal-target" data-delay="3">
                <Link to="/workspace" className="btn-primary">
                  Открыть сервис
                </Link>
                <a href="#story" className="btn-ghost">
                  Разобрать кейс
                </a>
              </div>
            </div>

            {/* Правая колонка: скульптура этапа 2 */}
            <div className="hero-stage" aria-hidden="true">
              <HeroSculpture />
            </div>
          </div>
        </section>

        {/* ── 3. Index-strip ───────────────────────────────────────────────── */}
        <section
          className="section-gap"
          aria-label="Сигналы продукта"
          ref={indexRef}
        >
          <div className="index-strip">
            {indexMetrics.map((metric) => (
              <div key={metric.idx} className="index-strip-item reveal-target">
                <span className="index-strip-idx">{metric.idx}</span>
                <div className="index-strip-body">
                  <span className="index-strip-label">{metric.label}</span>
                  <span className="index-strip-value">{metric.value}</span>
                  <p className="index-strip-copy">{metric.copy}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── 4. Story / How it works ──────────────────────────────────────── */}
        <section
          id="story"
          className="story-section section-gap"
          aria-labelledby="story-heading"
          ref={storyRef}
        >
          <div className="story-inner">
            {/* Левая колонка: контекст */}
            <div className="story-copy">
              <h2
                id="story-heading"
                className="story-title display-md reveal-target"
              >
                Как это работает и какую задачу решает.
              </h2>
              <p className="story-text body-prose reveal-target" data-delay="1">
                Вручную выдача Avito бесполезна для сравнения: в ней мешаются
                чужие регионы, федеральные объявления и свежие карточки без
                истории. Сервис проходит по выбранным городам, оставляет только
                местные объявления, отсекает пустышки, считает просмотры за
                день и сводит города в одну сравнимую таблицу. Отдельная
                разбивкой по метро для Москвы и Санкт-Петербурга.
              </p>
            </div>

            {/* Правая колонка: список фич */}
            <ul className="story-feature-list reveal-target" data-delay="2">
              {storyFeatures.map((item, i) => (
                <li key={item.title} className="story-feature-item">
                  <span className="story-feature-idx">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <div className="story-feature-content">
                    <p className="story-feature-title">{item.title}</p>
                    <p className="story-feature-desc">{item.desc}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>

      </div>

      {/* -- 5. Transition Chamber: полноширинная, вне контейнера ---------- */}
      <section
        className="transition-chamber section-gap"
        aria-label="Transition"
        ref={chamberRef}
      >
        <div className="page-container">
          <p className="chamber-phrase reveal-target">
            От разрозненной выдачи к сравнимой картине рынка.
          </p>
          <p className="chamber-sub reveal-target">
            Инструмент готов к работе
          </p>
        </div>
      </section>

      <div className="page-container">

        {/* ── 6. Workspace teaser ──────────────────────────────────────────── */}
        <section
          className="teaser-section section-gap"
          aria-label="Preview сервиса"
          ref={teaserRef}
        >
          <div className="teaser-layout reveal-target">
            <div className="teaser-heading">
              <p className="mono-kicker">Preview сервиса</p>
              <h2 className="teaser-title display-md">
                Интерфейс одним взглядом.
              </h2>
              <p className="teaser-body body-prose">
                Строка поиска, фильтры, выбор городов, таблица результатов.
                Запрос отправляется, города парсятся параллельно, результат
                приходит в таблицу с сортировкой.
              </p>
            </div>

            <Link to="/workspace" className="btn-primary" style={{ alignSelf: "flex-start" }}>
              Перейти в workspace
            </Link>
          </div>

          <div className="section-gap" style={{ marginTop: "2rem" }}>
            <ServiceTeaser />
          </div>
        </section>

        {/* ── 7. Author outro ──────────────────────────────────────────────── */}
        <section
          className="author-section section-gap"
          aria-label="Об авторе"
          ref={authorRef}
        >
          <p className="mono-kicker reveal-target">Об авторе</p>
          <h2 className="author-title display-md reveal-target" data-delay="1">
            Начиналось как инструмент для себя.
          </h2>
          <p className="author-copy body-prose reveal-target" data-delay="2">
            Нужно было быстро понимать, где на Avito реальный спрос, а не
            разбавленная выдача. Готового решения не нашёл, собрал своё.
            С нейросетями это стало быстрее: идея от постановки до
            работающего сервиса за несколько дней. Avito Research и есть
            этот инструмент.
          </p>
        </section>

        {/* ── 8. Footer ────────────────────────────────────────────────────── */}
        <footer className="site-footer section-gap" role="contentinfo">
          <p className="footer-left">
            &copy; 2026 Avito Research
          </p>
          <nav className="footer-right" aria-label="Ссылки футера">
            <Link to="/workspace" className="footer-link">
              Workspace
            </Link>
          </nav>
        </footer>

      </div>
    </main>
  );
}
