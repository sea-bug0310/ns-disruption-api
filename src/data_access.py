"""Reading data from Supabase for the dashboard (no Streamlit in here, so it can be tested)."""

# Supabase (PostgREST) returns at most 1000 rows per request, however many you ask for. A plain
# select("*") therefore silently returns only the first 1000 rows of a bigger table.
PAGE_SIZE = 1000


def fetch_all_rows(client, table, order_by="id", **equals):
    """Return every row of a table or view as a list of dicts, read page by page.

    `order_by` must be a unique column: without a stable order, pages could overlap or skip rows.
    `equals` adds filters, e.g. is_active=True.

    The offset is the number of rows received so far (not a multiple of PAGE_SIZE), and the loop
    ends on an empty page, so it still returns everything if the server's row cap is lower than
    PAGE_SIZE. The price is one extra, empty request at the end.
    """
    rows = []

    while True:
        query = client.table(table).select("*")

        for column, value in equals.items():
            query = query.eq(column, value)

        batch = (
            query.order(order_by)
            .range(len(rows), len(rows) + PAGE_SIZE - 1)
            .execute()
            .data
        )

        if not batch:
            break

        rows.extend(batch)

    return rows
