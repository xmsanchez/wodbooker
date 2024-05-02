alter table user add column force_login boolean default false;
alter table user add column mail_permission_success boolean default true;
alter table user add column mail_permission_failure boolean default true;
update user set mail_permission_success = false;