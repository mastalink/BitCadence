# Schedules

Open `/console` and choose **Schedules**, or run `bitcadence schedule` in a
terminal. Both show each schedule as a sentence, such as **Every weekday at
2:00 AM**, with when it runs next and an On/Off switch. No file is ever shown.

## Add one

Pick what should run, how often (Every day, Every weekday, Certain days, Every
hour) and the time. The browser's **Time zone (IANA)** starts with its detected zone and is editable. Review the validated zone and next occurrence including date/offset; Save stays disabled until the current choices have a valid matching preview. Unknown zones change nothing. For example, choose `America/New_York` or `UTC`.

Existing schedules are not migrated by this form. Legacy clients/CLI defaults and Ask repeat retain their existing server-default path; inspect the saved zone and next run on remote gateways. This uses the existing scheduler, including its existing DST behavior.

```
bitcadence schedule
bitcadence schedule add
bitcadence schedule add --what "Nightly dependency audit" --when "every weekday at 2 AM"
bitcadence schedule off "Nightly dependency"
bitcadence schedule on "Nightly dependency"
```

`--when` takes plain words: `every day at 7 am`, `every weekday at 2 AM`,
`every Monday, Wednesday and Friday at 9:15 pm`, `every hour`. A time without AM
or PM is asked about, not guessed. Words that cannot be understood get one
sentence back and nothing is saved:

```
I didn't understand "every second tuesday". Try "every weekday at 2 AM".
```

`--yes` saves without asking.

## What is behind it

The choices are written to `~/.mco/schedules.yaml`, the same file the original
`mco schedule` commands use (`init`, `list`, `enable`, `disable`, `reset`, `tick`,
`run` all still work). A new schedule is appended as text, so comments in a file
you edited by hand are kept, and the result is read back before it is saved. Only
things already defined as work to run can be scheduled; open-a-page and
open-an-app entries are not offered.

The console uses `GET /api/schedules`, `POST /api/schedules/preview`, `POST
/api/schedules` and `POST /api/schedules/{id}/enabled`. Reading needs `jobs:read`;
adding or switching one needs `agents:manage`.
