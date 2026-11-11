# Postgres roles, views and functions: who is checked for what

Learning note, written 2026-11-11 after a real bug in this project (the dashboard failed with
`permission denied for function stop_country_class`). Read it when you want to understand why it
happened, and use the checklist and exercises at the end.

## 1. Who is asking: roles

Every query runs as a **role** (a user with a set of permissions). In Supabase:

| Role | Who uses it | Typical rights |
|---|---|---|
| `anon` | Your dashboard. The `SUPABASE_KEY` in `secrets.toml` is the "anon key", so Supabase runs every dashboard query as `anon` | Only what we grant it. Here: read a few views |
| `authenticated` | Logged-in app users (not used here) | Same grants as `anon` in this project |
| `service_role` | Server code with the service key | Bypasses row level security. Never put it in a browser app |
| `postgres` | The pipeline (`DATABASE_URL`), the SQL editor | Owner of everything, can do everything |

The anon key is inside your Streamlit app, so treat it as semi-public: whatever `anon` can do,
anyone with the key can do. Keep `anon` to read-only access on a few views.

## 2. What a role needs, per kind of object

- **Table:** a `GRANT SELECT` (or INSERT, ...) on it. On top of that, if **row level security (RLS)**
  is on, the role also needs a *policy* that lets it see rows. RLS on with no policy means "no
  rows for anyone except the owner". Table owners are not subject to RLS.
- **View:** `GRANT SELECT` on the view.
- **Function:** `EXECUTE`. Postgres gives EXECUTE to everyone (`PUBLIC`) by default when a
  function is created; Supabase also grants it to `anon` and `authenticated`.

## 3. The rule that caused the bug

A view is a stored query. When `anon` reads a view:

1. **Tables inside the view** are read with the **view owner's** rights (usually `postgres`).
   That is why `anon` can read data through a view while the underlying table is locked to it by
   RLS. This is the trick we use on purpose (`dashboard_disruptions`, `ingestion_gaps`, ...).
   (Postgres 15 can reverse this with `WITH (security_invoker = true)`; we don't use it.)
2. **Functions called inside the view** are checked against **whoever runs the query**
   (`anon`), not the owner. If `anon` has no EXECUTE on the function: `permission denied for
   function ...`.

The same "who asks" question also applies *inside* the function:

- `SECURITY INVOKER` (the default): the function body runs with the rights of the caller. If it
  reads a locked table, it sees nothing (or errors).
- `SECURITY DEFINER`: the function body runs with the rights of its **owner**. Use it when the
  function must read something the caller cannot. Always add `SET search_path = public, pg_temp`
  so the owner's rights cannot be redirected to a look-alike object in another schema.

## 4. What went wrong here

`sql/006` created `stop_country_class(stop)` (it looks a stop up in `ref_station_names`, a table
locked by RLS) and a view `disruption_lines` that calls it. I made two mistakes:

1. I assumed functions behave like tables ("the view runs it as the owner") and **revoked EXECUTE
   from `anon`** to keep it tidy. The dashboard (`anon`) then got `permission denied`.
2. My tests ran as `postgres`, who may do everything, so they passed. One test even asserted
   that `anon` could *not* run the function, i.e. it checked for the bug as if it were the goal.

The fix (`sql/007`, and a corrected `006`): `GRANT EXECUTE ... TO anon, authenticated`, **and**
make the function `SECURITY DEFINER`. Both are needed: with only the grant, the function would run
as `anon`, see an empty `ref_station_names` (RLS), return NULL for every stop, and every line
would silently count as domestic, which is a worse bug because nothing errors.

## 5. How to test as the dashboard, not as the owner

In the Supabase SQL editor (or any psql session as `postgres`):

```sql
SET ROLE anon;                      -- from now on you are the dashboard
SELECT * FROM disruption_lines LIMIT 5;
SELECT * FROM ref_station_names;    -- should FAIL: the table is locked to anon
RESET ROLE;                         -- back to postgres
```

And ask Postgres what a role may do:

```sql
SELECT has_table_privilege('anon', 'public.disruption_lines', 'SELECT');
SELECT has_function_privilege('anon', 'stop_country_class(text)', 'EXECUTE');
```

Check **results**, not only "no error": here the numbers were wrong without any error being
possible if the function had run as `anon`.

## 6. Checklist for every new database object

- Table: is RLS on? Which role needs which GRANT? (Default for this project: RLS on, no policy,
  read through a view.)
- View: `GRANT SELECT` to `anon` / `authenticated`. Does it call a function? Then see below.
- Function: who calls it? Does `anon` need EXECUTE? Does the body read something `anon` cannot,
  so it needs `SECURITY DEFINER` plus a fixed `search_path`?
- Test **as `anon`** and check the answers are right.
- Remember: a function `anon` may execute is also reachable as an API call in Supabase. Make
  sure it only returns something you are happy to show.

## 7. Try it yourself (on a scratch Postgres, not on Supabase)

1. Create a table `secret(n int)` with RLS on and no policy, insert a row, create a view
   `v AS SELECT * FROM secret`, `GRANT SELECT` on the view to a new role. As that role, query the
   view and the table. Which works, and why?
2. Add a function `f()` that returns `count(*)` from `secret`, and a view `v2 AS SELECT f()`.
   As the new role, query `v2` with and without `GRANT EXECUTE`. What do you get each time?
3. Make `f()` `SECURITY DEFINER SET search_path = public, pg_temp`. Query `v2` again and compare
   the number with step 2.
4. Write down in one sentence the difference between "who may execute a function" and "whose
   rights the function body uses".
5. In this repo: which of our views would break if you set `security_invoker = true` on them,
   and why?

Related: ADR 0004 (`docs/decisions/0004-domestic-or-international-lines.md`),
`sql/006_station_names_and_line_country.sql`, `sql/007_stop_country_class_access.sql`.
