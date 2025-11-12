-- Add athlete_id, profile_picture_url, and athlete_name fields to user table
alter table user add column athlete_id varchar(128);
alter table user add column profile_picture_url varchar(512);
alter table user add column athlete_name varchar(128);

