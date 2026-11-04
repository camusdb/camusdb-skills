# CamusDB JSONB queries

Read this file when you read, filter, build, or change JSON documents in SQL.
The `jsonb` surface follows PostgreSQL. The differences are listed at the end.
Column design and index choice belong to the `camusdb-schema-design` skill.

Source page: https://camusdb.github.io/docs/json.

## The type

- `jsonb` is the type. `json` is an alias. `SHOW CREATE TABLE` shows `JSONB`.
- The words `json` and `jsonb` are not reserved. A table or column can use them.
- A cell holds one document, or SQL `NULL`.
- JSON `null` differs from SQL `NULL`: `'null'::jsonb IS NULL` is false.
- A `jsonb` column cannot join a primary key, `UNIQUE`, an ordered index, or a
  foreign key. `array(jsonb)` is not a type. Write `'[...]'::jsonb`.

## Write a document

```sql
INSERT INTO events (id, payload) VALUES (1, '{"user": {"id": 7}, "kind": "login"}');
SELECT JSONB '{"a": 1}', JSON '[1, 2]', '{"a": 1}'::jsonb, CAST(@p AS jsonb);
SELECT jsonb_build_object('a', 1), to_jsonb(42);
```

- Invalid JSON text fails with `CADB0418` and writes nothing. A trailing comma,
  a leading zero, `NaN`, a lone surrogate, and `\u0000` are invalid.
- Only text becomes a document through a cast. Use `to_jsonb` for another type.
- The stored form is canonical. Keys sort by UTF-8 byte length, then by bytes.
  The last value of a repeated key wins. White space is dropped.
- Numbers are exact: `1.50` reads back as `1.50`. `1e3` reads back as `1000`.

## Read a document

- `doc::string` and `CAST(doc AS string)` give the document text.
- `->>` and `#>>` give the text of one part.
- A scalar document casts as in PostgreSQL: `('42'::jsonb)::int64`, `::float64`,
  `::numeric`, `::bool`. The wrong kind fails.
- A cast to `int64` rounds half away from zero. `doc ->> 'a'` gives a string, and
  a string must spell an integer. Cast `doc -> 'a'` to round a number.

## Operators

| Operator | Right operand | Result | Example |
| --- | --- | --- | --- |
| `->` | text key or integer index | `jsonb` | `doc -> 'user'`, `doc -> -1` |
| `->>` | text key or integer index | `string` | `doc ->> 'kind'` |
| `#>` | path | `jsonb` | `doc #> '{user,tags,0}'` |
| `#>>` | path | `string` | `doc #>> ARRAY['user', 'id']` |
| `@>` | `jsonb` | `bool` | `doc @> '{"kind": "login"}'` |
| `<@` | `jsonb` | `bool` | `'{"kind": "login"}' <@ doc` |
| `?` | text | `bool` | `doc ? 'kind'` |
| `?\|` | text array | `bool` | `doc ?\| ARRAY['a', 'b']` |
| `?&` | text array | `bool` | `doc ?& '{a,b}'` |
| `\|\|` | `jsonb` | `jsonb` | `doc \|\| '{"seen": true}'` |
| `-` | text, text array, or integer | `jsonb` | `doc - 'tmp'`, `doc - 0` |
| `#-` | path | `jsonb` | `doc #- '{user,tags}'` |

- A path is `ARRAY['a', 'b']` or the text form `'{a,b}'`. An integer step picks
  an array element. A negative index counts from the end.
- A missing key, index, or path gives SQL `NULL`, not an error.
- A top-level scalar acts as a one-element array for `-> 0`, `?`, and `@>`.
- `@>` means every part of the right document is in the left one. Array order and
  repeats do not matter. Below the top level, a scalar does not contain an array.
- `?` tests a top-level object key, a top-level array string, or the scalar string.
- `||` merges two objects, and the right side wins. It concatenates two arrays.
  A scalar operand becomes a one-element array.
- A text operand where a document belongs is parsed as a document.

These operators bind tighter than comparison, `IS`, and `IN`. So these forms work:

```sql
SELECT id FROM events WHERE payload ->> 'deleted_at' IS NULL;
SELECT id FROM events WHERE payload ->> 'kind' IN ('login', 'logout');
SELECT id FROM events WHERE payload -> 'user' ->> 'id' = '7';
```

Lexer traps:

- `x <@p` means `x < @p`. Write `x <@ @p` for the operator with a placeholder.
- `x #-1` is a path delete. Write `x # -1` for XOR.
- A driver that rewrites `?` (JDBC, ODBC) can break `?`, `?|`, and `?&`. Use
  `jsonb_exists`, `jsonb_exists_any`, and `jsonb_exists_all` there.

## Compare, sort, and group

`=`, `<`, `ORDER BY`, `GROUP BY`, `DISTINCT`, and joins use the PostgreSQL rules:

- Kinds sort `Object > Array > Boolean > Number > String > Null`.
- More pairs or more elements sort higher. Then members compare in stored order.
- Numbers compare by value: `'1.0'::jsonb = '1'::jsonb` is true.
- An empty top-level array sorts below every top-level scalar.
- A text operand reads as a document. Text that is not JSON equals no document.

## Functions

Most functions here return SQL `NULL` for a SQL `NULL` argument. The builders differ,
as the table says. Aliases are in parentheses.

| Function | Result |
| --- | --- |
| `to_jsonb(value)` (`to_json`) | The value as a document. A date, datetime, bytes, or non-finite float becomes a string. |
| `jsonb_build_object(k1, v1, ...)` | An object from key and value pairs. A SQL `NULL` value becomes JSON `null`. A `NULL` key is an error. |
| `jsonb_build_array(v1, ...)` | An array. A SQL `NULL` element becomes JSON `null`. |
| `jsonb_object(text_array)`, `jsonb_object(keys, values)` | An object of string values. |
| `jsonb_typeof(doc)` (`json_typeof`) | `object`, `array`, `string`, `number`, `boolean`, or `null`. |
| `jsonb_array_length(doc)` | Element count. An object or a scalar is an error. |
| `jsonb_extract_path(doc, step, ...)` | Same as `#>`. |
| `jsonb_extract_path_text(doc, step, ...)` | Same as `#>>`. |
| `jsonb_exists(doc, key)` | Same as `?`. |
| `jsonb_exists_any(doc, keys)`, `jsonb_exists_all(doc, keys)` | Same as `?\|` and `?&`. |
| `jsonb_set(doc, path, value [, create_missing])` | Replace the value at the path. `create_missing` defaults to true. |
| `jsonb_set_lax(doc, path, value [, create_missing [, treatment]])` | `jsonb_set` with a rule for a SQL `NULL` value: `'raise_exception'`, `'use_json_null'` (default), `'delete_key'`, `'return_target'`. |
| `jsonb_insert(doc, path, value [, insert_after])` | Insert into an array, or add a new key. An existing key is an error. |
| `jsonb_strip_nulls(doc)` (`json_strip_nulls`) | Remove object fields with a JSON `null`, at every level. |
| `jsonb_pretty(doc)` | Text with 4-space indents. |

A SQL `NULL` element in the path of a change function is an error.

### Aggregates

| Aggregate | Result |
| --- | --- |
| `jsonb_agg(value)` (`json_agg`) | An array of the group's values. A SQL `NULL` becomes JSON `null`. |
| `jsonb_object_agg(key, value)` (`json_object_agg`) | An object. A `NULL` key is an error. The last repeated key wins. |

A group of zero rows gives SQL `NULL`. A built document over 268,435,455 bytes
fails with `CADB0422`. `jsonb_agg(x ORDER BY y)` is not supported.

### Set-returning functions (in `FROM` only)

| Function | Columns |
| --- | --- |
| `jsonb_each(doc)` | `key string`, `value jsonb` |
| `jsonb_each_text(doc)` | `key string`, `value string` |
| `jsonb_object_keys(doc)` | One `string` column, named after the relation. |
| `jsonb_array_elements(doc)` | `value jsonb` |
| `jsonb_array_elements_text(doc)` | `value string` |

```sql
SELECT key, value FROM jsonb_each('{"a": 1, "b": [2]}');
SELECT e.value FROM jsonb_array_elements(@doc) AS e WHERE e.value ->> 'ok' = 'true';
SELECT k, v FROM jsonb_each(@doc) AS e(k, v);
```

- The argument is a literal, a parameter, or an expression without columns.
- `FROM t, jsonb_each(t.doc)` is `LATERAL`, which is not supported.
- A document of the wrong kind fails before the first row.
- `key` is reserved, but `SELECT key` and `e.key` work as column references.

### The text functions

`json_valid`, `json_type`, `json_extract`, `json_value`, `json_contains`, and
`json_array_length` take text and use `$.a.b[0]` paths. A `jsonb` argument is read
as its text. See [functions.md](functions.md).

## GIN indexes in queries

The planner can use a GIN index for a top-level `WHERE` conjunct when the other
operand is a constant, a parameter, or an expression without columns:

| Conjunct | Served |
| --- | --- |
| `col @> doc`, `doc <@ col` | Yes, except `col @> '{}'`, `'[]'`, or a scalar. |
| `col ? key`, `col ?\| keys`, `col ?& keys` | Yes. |
| `col ->> 'k' = text` | Yes, unless the text starts with `{` or `[`. |
| `col -> 'k' = doc` | Yes. |

The index is not used under `OR` or `NOT`, for `col <@ doc`, or in a join. The
function forms such as `jsonb_exists(col, key)` are served the same way.

## Limits and errors

| Code | Name | Cause |
| --- | --- | --- |
| `CADB0418` | `InvalidJsonText` | Text is not a document that `jsonb` can store. |
| `CADB0419` | `JsonNestingTooDeep` | A document nests deeper than 512 levels. The limit is fixed. |
| `CADB0422` | `JsonbValueTooLarge` | A built aggregate exceeds 268,435,455 bytes. |
| `CADB0559` | `JsonbValueCorrupt` | A stored cell does not decode. No partial document returns. |

A number may have 131,072 digits before the point and a scale of 16,383. Each
distinct leaf of an indexed document counts against the transaction mutation limit.

## Over the wire

- HTTP sends the document as native JSON. JSON `null` and SQL `NULL` then look the
  same. Set `"jsonbAsText": true` on the request to keep them apart.
- gRPC sends the text form in `Value.json_value`. An old client reads `NULL`.

## Differences from PostgreSQL

| Topic | CamusDB |
| --- | --- |
| `json` type | An alias of `jsonb`. The input text is not kept. |
| `jsonb_path_ops` | Builds the same index as `jsonb_ops`. |
| Precedence | Operators sit above `IS` and `IN`, not at the `\|\|` level. |
| A text column as a document operand | Parsed as a document, not refused. |
| Non-JSON text compared with a document | Equals no document, not refused. |
| `jsonb_agg(x ORDER BY y)` | Not supported. |
| `LATERAL` | Not supported. |
| `jsonpath`, `@?`, `@@`, `jsonb_path_query` | Not supported. |
| `doc['key']` | Not supported. Write `doc -> 'key'`. |
| `row_to_json`, `jsonb_to_record`, `jsonb_populate_record` | Not supported. |
| Expression index on `(doc ->> 'k')` | Not supported yet. |
