alter table opportunities add column event_start timestamptz,
    add column event_all_day boolean not null default false,
    add column event_update_pending boolean not null default false,
    add column cancelled boolean not null default false;
