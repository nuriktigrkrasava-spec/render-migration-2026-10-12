import { PGlite } from '@electric-sql/pglite';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const db=new PGlite();
await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
const base=new URL('../supabase/migrations',import.meta.url).pathname;
for (const name of (await fs.readdir(base)).filter(n=>n.endsWith('.sql')).sort()) await db.exec(await fs.readFile(`${base}/${name}`,'utf8'));
const s=(await db.query("insert into students(telegram_id,name) values('test','test') returning id")).rows[0].id;
const b=(await db.query("insert into books(title,level) values('test',1) returning id")).rows[0].id;
const p=(await db.query('insert into parts(book_id,"order",title) values($1,1,\'test\') returning id',[b])).rows[0].id;
const q=(await db.query("insert into questions(part_id,type,text) values($1,'choice','test') returning id",[p])).rows[0].id;
const rows=[{question_id:q,selected_option_index:0}];
const call=()=>db.query('select submit_part_atomic($1,$2,$3::jsonb) as ok',[s,p,JSON.stringify(rows)]);
assert.equal((await call()).rows[0].ok,true);
assert.equal((await call()).rows[0].ok,false);
assert.equal((await db.query('select count(*) as n from answers')).rows[0].n,1);
await db.exec('delete from answers');
assert.equal((await call()).rows[0].ok,true); // legacy empty attempt recovers
await db.exec('delete from answers; delete from attempts');
try { await db.query('select submit_part_atomic($1,$2,$3::jsonb)',[s,p,JSON.stringify([{question_id:q,selected_option_index:'invalid'}])]); assert.fail('invalid write must fail'); } catch(e) { if(e.code===undefined) throw e; }
assert.equal((await db.query('select count(*) as n from attempts')).rows[0].n,0); // transaction rollback
await db.query("insert into questions(part_id,type,text) select $1,'open','question' from generate_series(1,1100)",[p]);
assert.equal((await db.query('select get_scoring_catalog() as catalog')).rows[0].catalog.questions.length,1101);
await db.exec('set role service_role');
await call();
assert.equal((await db.query('select get_progress_attempts() as attempts')).rows[0].attempts[0].answers.length,1);
await db.exec('set role anon');
try { await db.query('select * from students'); assert.fail('anonymous read must fail'); } catch(e) { assert.equal(e.code,'42501'); }
try { await db.query('select get_scoring_catalog()'); assert.fail('anonymous RPC must fail'); } catch(e) { assert.equal(e.code,'42501'); }
console.log('PASS: all migrations, atomic submit, repeated taps, legacy recovery, rollback, 1101 questions, progress RPC and denied anonymous access');
await db.close();
