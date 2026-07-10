# Sdilej.cz Upload Flow

Observed on 2026-07-10.

## Login

1. `GET https://sdilej.cz/prihlasit`
2. `POST https://sdilej.cz/sql.php`

Form fields:

- `login` - email or username
- `heslo` - password

Successful login redirects to `/nastaveni` and sets the authenticated `SDILEJ`
cookie.

## Upload Page

`GET https://sdilej.cz/upload` returns a Blueimp File Upload form:

```html
<form id="fileupload" action="https://uploadweb2.sdilej.cz/_upload/" method="POST" enctype="multipart/form-data">
  <input type="hidden" name="user_id" value="...">
  <input type="file" name="files[]" multiple>
</form>
```

The page JS overrides the form action to:

```text
https://uploadweb2.sdilej.cz/upload/index.php
```

and sends chunks with `maxChunkSize = 2 * 1024 * 1024`.

## Upload Request

Single request:

```http
POST https://uploadweb2.sdilej.cz/upload/index.php
Referer: https://sdilej.cz/upload
Content-Type: multipart/form-data

user_id=<id>
files[]=<file>
```

Chunked request:

```http
POST https://uploadweb2.sdilej.cz/upload/index.php
Referer: https://sdilej.cz/upload
Content-Range: bytes <start>-<end>/<total>
Content-Type: multipart/form-data

user_id=<id>
files[]=<chunk bytes, original filename>
```

Intermediate chunk response:

```json
{"files":[{"name":"file.mp4","size":2097152,"type":"video/mp4"}]}
```

Final response includes the public URL:

```json
{"files":[{"name":"file.mp4","size":123456789,"type":"video/mp4","url":"https://sdilej.cz/123456/file.mp4"}]}
```

## Limits

The public FAQ says browser uploads support large files and recommends Sdilej.cz
Manager or ZOOM uploader for bigger files. The observed browser implementation is
chunked, which is suitable for the sktorrent 720p files used by this project.

