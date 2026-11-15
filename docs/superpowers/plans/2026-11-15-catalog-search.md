# Catalog Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Search films by title, people (actors/directors), studios and collections, and browse by genre, in the Svelte phone app.

**Architecture:** New cached `TmdbClient` methods call TMDB's dedicated endpoints in parallel. New `/api` GET endpoints shape the results into Card lists with `in_library`. The Svelte Search tab groups the results, and four new pushed routes show a person, studio, collection or genre with a film grid.

**Tech Stack:** Python 3.12, FastAPI, httpx, pytest+anyio (MockTransport); Svelte 5 + Vite (app/web).

**Spec:** `docs/superpowers/specs/2026-11-15-catalog-search-design.md`

## Global Constraints
- Branch `catalog-search`. Commit messages are a single title line, with no body and no AI attribution.
- TMDB TTLs: `HOUR` (3600 s) for detail, genre and discover pages; `SEARCH_TTL` (600 s) for searches. The cache key includes the method and every argument (query, id, page).
- Upstream failures map to 502 `{"error":"upstream","service":"tmdb"}`. A TMDB 404 maps to 404 `{"error":"not_found"}` (existing `_call`).
- `poster_path`, `profile_path` and `logo_path` stay raw TMDB paths in JSON. The UI prefixes `https://image.tmdb.org/t/p/{w185|w342}`.
- The Svelte app has no new runtime deps and never uses `{@html}` with API strings. All new copy is in en and fr (language from navigator.language).
- Tests: `cd app && .venv/bin/python -m pytest -q`. Web: `cd app/web && npm run check && npm run build`.

## Review Focus
1. A query that matches only a person (e.g. "Tom Hanks") or only a studio: Films may be empty while People or Studios are not, so the UI must show those groups, not the "no results" state. Test `test_search_groups_person_only` (Task 2).
2. One of the four parallel TMDB searches fails while the others succeed: the request returns the groups that worked rather than a 502. It returns 502 only if all four fail. Test `test_search_all_partial_failure` (Task 1).
3. A person who both acts in and directs the same film, e.g. Clint Eastwood: the film appears once. Test `test_person_dedupes_cast_and_director` (Task 1).
4. Paging past the end on studio and genre pages, and `total_pages` capped by TMDB at 500: "Load more" disappears at the last page. Test `test_genre_page_passes_page_and_total` (Task 2), plus the UI hides the button when `page >= total_pages`.
5. People and studios with no image: an initials circle or a name tile, never a broken image. This is checked in the Task 3 screenshots.

---

### Task 1: TMDB client methods

**Files:**
- Modify: `app/cinema/tmdb.py`
- Test: `app/tests/test_tmdb.py`

**Interfaces:**
- Produces, in `cinema.tmdb`, frozen dataclasses:
  - `PersonHit(id: int, name: str, profile_path: str | None, known_for_department: str | None)`
  - `CompanyHit(id: int, name: str, logo_path: str | None)`
  - `CollectionHit(id: int, name: str, poster_path: str | None, backdrop_path: str | None = None)`
  - `Genre(id: int, name: str)`
  - `SearchAll(films: list[MovieCard], people: list[PersonHit], studios: list[CompanyHit], collections: list[CollectionHit])`
  - `Paged(films: list[MovieCard], page: int, total_pages: int)`
- Produces, as `TmdbClient` async methods:
  - `search_all(query: str) -> SearchAll`. TTL `SEARCH_TTL`. The four calls run concurrently via `asyncio.gather(..., return_exceptions=True)`. A failed call yields an empty group. If all four fail, it raises the first `TmdbError`.
    - **People:** only `known_for_department` in {"Acting","Directing"}. Sort by popularity descending, keep the top 12, and drop entries without `profile_path` unless the name equals the query (case-insensitive).
    - **Studios:** entries with a `logo_path` first, then the rest, keeping the top 6.
    - **Collections:** the top 6.
  - `person(person_id: int) -> tuple[PersonHit, list[MovieCard]]`. TTL `HOUR`. Calls `/person/{id}` and `/person/{id}/movie_credits`. Films are the cast entries plus the crew entries with `job == "Director"`, deduped by id and sorted by `popularity` descending.
  - `company(company_id: int, page: int = 1) -> tuple[CompanyHit, Paged]`. TTL `HOUR`. Calls `/company/{id}` and `/discover/movie` with `with_companies`, `sort_by=popularity.desc` and `page`.
  - `collection(collection_id: int) -> tuple[CollectionHit, list[MovieCard]]`. TTL `HOUR`. Parts are sorted by `release_date` ascending, with empty dates last.
  - `genres() -> list[Genre]`. TTL `HOUR`. Calls `/genre/movie/list`.
  - `genre_movies(genre_id: int, page: int = 1) -> tuple[Genre, Paged]`. TTL `HOUR`. Calls `/discover/movie` with `with_genres`, `sort_by=popularity.desc`, `vote_count.gte=50` and `page`. The genre name comes from `genres()`. An unknown id raises `TmdbError("unknown genre", status=404)`.
  - `total_pages` is `min(data["total_pages"], 500)`.

- [ ] **Step 1: Write failing tests** in `app/tests/test_tmdb.py`, using the file's existing `make(handler, clock=None)` MockTransport helper:
  - `test_search_all_groups_and_filters`: the handler serves the four search paths. Assert:
    - films are parsed;
    - a person with department "Sound" is dropped;
    - people are sorted by popularity;
    - a studio with a logo comes before one without;
    - each request carries `query` and `language`.
  - `test_search_all_partial_failure`: `/search/company` returns 500 and the others 200. The result has films and people, and `studios == []`. A second test variant where all four return 500 asserts `TmdbError`.
  - `test_person_dedupes_cast_and_director`: the same movie id appears in cast and in crew as Director. It appears once, and films are ordered by popularity. A crew entry with job "Producer" is excluded.
  - `test_company_and_genre_paging`: `company(7, page=2)` sends `with_companies=7&page=2`. A `total_pages` of 9000 is capped to 500. `genre_movies(28, page=3)` sends `with_genres=28`, `vote_count.gte=50` and `page=3`, and returns `Genre(28, "Action")` from a mocked `/genre/movie/list`.
  - `test_collection_parts_sorted_by_date`.
- [ ] **Step 2:** Run `cd app && .venv/bin/python -m pytest -q tests/test_tmdb.py`. Expected: the new tests FAIL with ImportError or AttributeError.
- [ ] **Step 3:** Implement the dataclasses and methods above in `app/cinema/tmdb.py`, reusing `_get`, `_cached` and `_card`. Wrap malformed payloads the way existing methods do.
- [ ] **Step 4:** Run the full suite. Expected: all pass.
- [ ] **Step 5:** Commit: `git commit -m "feat: search TMDB people, studios, collections and genres"`

### Task 2: API endpoints

**Files:**
- Modify: `app/cinema/api.py`
- Test: `app/tests/test_api.py` (update the existing `/search` assertions from `results` to `films`)

**Interfaces:**
- Consumes: the Task 1 methods and types, and the existing `_card(c, store)`, `_call(service, errors, coro)` and 404 mapping.
- Produces (GET, JSON):
  - `/api/search?q=` returns `{"films":[Card], "people":[{"id","name","profile_path","known_for_department"}], "studios":[{"id","name","logo_path"}], "collections":[{"id","name","poster_path"}]}`. An empty q gives all four as `[]`, with no TMDB call.
  - `/api/people/{id}` returns `{"person":{...}, "films":[Card]}`.
  - `/api/studios/{id}?page=1` returns `{"studio":{...}, "films":[Card], "page":int, "total_pages":int}`.
  - `/api/collections/{id}` returns `{"collection":{"id","name","poster_path","backdrop_path"}, "films":[Card]}`.
  - `/api/genres` returns `{"genres":[{"id","name"}]}`.
  - `/api/genres/{id}?page=1` returns `{"genre":{"id","name"}, "films":[Card], "page", "total_pages"}`.
  - `page` is validated as an int in 1..500 (FastAPI `Query(1, ge=1, le=500)`).

- [ ] **Step 1: Write failing tests** in `app/tests/test_api.py`:
  - `test_search_groups_person_only`: TMDB mock returns no films but one person. The response has `films == []` and `len(people) == 1`.
  - `test_search_empty_query_no_tmdb_call`.
  - `test_person_page_cards_have_in_library`: a store row for one of the films gives `in_library` true on that card.
  - `test_genre_page_passes_page_and_total`: `?page=2` reaches TMDB, `page == 2`, and `total_pages` is passed through. `?page=0` gives 422.
  - `test_unknown_person_is_404`: TMDB 404 gives 404 `{"error":"not_found"}`.
  - `test_search_tmdb_down_is_502` already exists. Keep it passing with the new shape (all four failing gives 502).
- [ ] **Step 2:** Run it and expect FAIL.
- [ ] **Step 3:** Implement the endpoints as `async def` handlers in `api.py`.
- [ ] **Step 4:** Run the full suite and expect all to pass.
- [ ] **Step 5:** Commit: `git commit -m "feat: expose people, studios, collections and genres in the phone API"`

### Task 3: Svelte search and browse screens

**Files:**
- Modify:
  - `app/web/src/screens/Search.svelte`
  - `app/web/src/lib/api.ts`
  - `app/web/src/lib/router.svelte.ts`
  - `app/web/src/lib/i18n.ts`
  - `app/web/src/App.svelte`
- Create:
  - `app/web/src/screens/Browse.svelte`: one screen for the person, studio, collection and genre routes, taking `kind` and `id`.
  - `app/web/src/components/PersonRow.svelte` and `app/web/src/components/TileRow.svelte`, or equivalent small components.
- Smoke-test backend: extend `/home/marlon/Projects/Cinema/.superpowers/sdd/ui-svelte/smoke_app.py` (outside the repo) so its fake TMDB serves the new paths.

**Interfaces:**
- Consumes: the Task 2 endpoints.
- Produces:
  - routes `#/person/{id}`, `#/studio/{id}`, `#/collection/{id}`, `#/genre/{id}`, pushed over the current tab like `#/movie/{id}`;
  - i18n keys in en/fr:
    - `people`: "People" / "Personnes"
    - `studios`: "Studios" / "Studios"
    - `collections`: "Collections" / "Collections"
    - `genres`: "Genres" / "Genres"
    - `load_more`: "Load more" / "Plus de résultats"
    - `films_with`: "Films with {name}" / "Films avec {name}"
    - `browse_empty`: "No films here yet" / "Aucun film ici pour l'instant"

- [ ] **Step 1:** Search tab, results state.
  - Sections in this order: Films (the existing 3-column grid), People (a horizontal row of 72 px round photos with name below), Studios (a row of logo tiles, w185 logos on a light tile), Collections (a row of poster tiles).
  - Hide empty sections. Show "no results" only when all four are empty.
  - Missing images: people get an initials circle, studios a name tile.
- [ ] **Step 2:** Search tab, idle state. Show genre chips from `/api/genres`, wrapping across lines with ≥44 px targets, above "Popular right now". Tapping a chip goes to `#/genre/{id}`.
- [ ] **Step 3:** Browse screen.
  - Header by kind:
    - person: round photo, name and the `films_with` title;
    - studio: logo or name;
    - collection: backdrop and name;
    - genre: name.
  - Then a film grid using the existing poster card, with the check badge, opening `#/movie/{id}`.
  - Studio and genre show "Load more" while `page < total_pages`. The button appends the next page and dedupes by id.
  - Errors use the existing `error` copy with retry. The back button returns to where the user came from, keeping scroll.
- [ ] **Step 4:** Run `npm run check && npm run build` and expect 0 errors. Run `cd app && .venv/bin/python -m pytest -q` and expect all to pass.
- [ ] **Step 5:** Screenshot at 390×844 in en and fr (headless Chromium, same method as the redesign; save to `/home/marlon/Projects/Cinema/.superpowers/sdd/ui-svelte/search-*.png`):
  - search idle with genre chips;
  - "tom hanks" results (people row shown even with few films);
  - a person page;
  - a studio page with Load more;
  - a genre page;
  - a person with no photo.
  Iterate until they look polished. Click through with no console errors.
- [ ] **Step 6:** Commit: `git commit -m "feat: browse films by people, studios, collections and genres"`
