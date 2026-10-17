"""Local-only adapter for the pinned Screen Time exporter."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta
import csv
import bisect
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import uuid

from screentime.sources.biome import (
    BiomeSource, STREAM, _segments, read_segment, stitch, _cf_to_utc,
)
from screentime.sources.knowledgec import KnowledgeCSource
from screentime.sources import UsageEvent
from screentime import taxonomy, launchd
from screentime.aggregates import rebuild_days
from screentime.config import load_settings
from screentime.db import connect, init_db, get_state, set_state, now_utc_iso, parse_timestamp
from screentime.health import denied_sources
from screentime.importer import sync_sources, event_key
from screentime.locking import process_lock, db_lock_path, LockBusyError

NETWORK_ATTEMPTS = 0


def deny_network(event, args):
    global NETWORK_ATTEMPTS
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        NETWORK_ATTEMPTS += 1
        raise PermissionError("local collector forbids network access")


class ReplayBiomeSource(BiomeSource):
    """Re-read all retained segments, including older delayed arrivals.

    Segment creation order is not event-time order after delayed iCloud sync.
    Sort transitions globally per device and dedupe identical transitions before
    stitching. SQLite upserts retain historical sessions pruned by Apple.
    """
    def __init__(self, library, *, ledger=None):
        super().__init__(library)
        self.ledger=ledger
        self.memory={}
        self.canonical={}
        self.current_transitions={}
        if ledger is not None:
            ledger.execute("""CREATE TABLE IF NOT EXISTS local_focus_transitions (
              device_id TEXT NOT NULL, cf_time REAL NOT NULL, bundle_id TEXT NOT NULL,
              gained INTEGER NOT NULL, PRIMARY KEY(device_id,cf_time,bundle_id,gained))""")
            ledger.commit()

    def read(self, state):
        events = []
        segment_count = 0
        self.canonical={}
        self.current_transitions={}
        for device in self.devices():
            transitions = []
            for path in _segments(self.library / STREAM / device.id):
                segment_count += 1
                transitions.extend(read_segment(path))
            self.current_transitions[device.id]=len(transitions)
            if self.ledger is not None:
                self.ledger.executemany("INSERT OR IGNORE INTO local_focus_transitions VALUES(?,?,?,?)",
                                        [(device.id, cf, bundle, int(gain)) for cf,bundle,gain in transitions])
                self.ledger.commit()
                ordered=[(r[0],r[1],bool(r[2])) for r in self.ledger.execute(
                    "SELECT cf_time,bundle_id,gained FROM local_focus_transitions WHERE device_id=? ORDER BY cf_time,rowid",(device.id,))]
            else:
                self.memory.setdefault(device.id,{})
                self.memory[device.id].update(dict.fromkeys(transitions))
                ordered=sorted(self.memory[device.id],key=lambda item:item[0])
            closed, _ = stitch(iter(ordered), None)
            reconstructed=[UsageEvent(device.id,bundle,_cf_to_utc(start),_cf_to_utc(end)) for bundle,start,end in closed]
            self.canonical[device.id]=reconstructed
            events.extend(reconstructed)
        return events, {"mode": "full_retained_replay", "segments": segment_count}


class ReplayKnowledgeCSource(KnowledgeCSource):
    def read(self, state):
        # Old creation timestamps can also arrive after the standard overlap.
        result=super().read(None)
        self.current_event_count=len(result[0])
        return result


def validate_settings(settings):
    if (settings.lookup_enabled or settings.influx_url or settings.influx_token
            or settings.ha_url or settings.ha_token
            or settings.timezone.key != "Asia/Tokyo"
            or not settings.sources.get("biome",True) or not settings.sources.get("knowledgec",True)):
        raise ValueError("local-only JST configuration required")


class WarningCounter(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.count = 0
    def emit(self, record):
        # Do not format or forward paths, device ids, bundle ids or raw errors.
        self.count += 1


def refresh_metadata(conn, settings):
    conn.execute("""CREATE TABLE IF NOT EXISTS local_app_metadata (
      bundle_id TEXT PRIMARY KEY, app_name TEXT, category TEXT,
      name_source TEXT NOT NULL, category_source TEXT NOT NULL)""")
    cache = {r["bundle_id"]: r for r in conn.execute("SELECT * FROM app_lookup")}
    for row in conn.execute("SELECT DISTINCT bundle_id FROM screen_events").fetchall():
        bundle = row[0]
        override = settings.apps.get(bundle, {})
        bundled = taxonomy.APPS.get(bundle)
        installed = cache.get(bundle)
        # Never promote heuristic guesses or any legacy online-cache entry.
        if installed and not str(installed["genre"] or "").startswith("mac"):
            installed = None
        values = []
        for field, index in (("name", 0), ("category", 1)):
            if override.get(field):
                values.append((str(override[field]), "user"))
            elif bundled:
                values.append((bundled[index], "bundled_mapping"))
            elif installed and installed[field]:
                values.append((installed[field], "installed_mac_mapping"))
            else:
                values.append((None, "unavailable"))
        conn.execute("""INSERT INTO local_app_metadata VALUES (?,?,?,?,?)
          ON CONFLICT(bundle_id) DO UPDATE SET app_name=excluded.app_name,
          category=excluded.category, name_source=excluded.name_source,
          category_source=excluded.category_source""",
          (bundle, values[0][0], values[1][0], values[0][1], values[1][1]))
    conn.execute("DROP VIEW IF EXISTS local_sessions")
    conn.execute("""CREATE VIEW local_sessions AS
      SELECT e.event_key, e.device_id, d.name AS device_name, d.platform, d.source,
      e.bundle_id, m.app_name, m.category, m.name_source, m.category_source,
      e.started_at_utc, strftime('%Y-%m-%dT%H:%M:%fZ',
        julianday(e.started_at_utc)+e.duration_s/86400.0) AS ended_at_utc,
      e.duration_s, date(e.started_at_utc,'+9 hours') AS date_jst
      FROM screen_events e JOIN devices d USING(device_id)
      LEFT JOIN local_app_metadata m USING(bundle_id)
      LEFT JOIN local_event_lifecycle l USING(event_key)
      WHERE COALESCE(l.active,1)=1""")
    conn.commit()


EXPORT_FIELDS = ("device_id", "device_name", "platform", "source", "bundle_id",
                 "app_name", "category", "name_source", "category_source",
                 "start", "end", "duration_s", "date", "end_date")


def session_rows(conn, tz):
    for row in conn.execute("SELECT * FROM local_sessions ORDER BY started_at_utc,event_key"):
        begin = parse_timestamp(row["started_at_utc"])
        end = begin + timedelta(seconds=row["duration_s"])
        item = {k: row[k] for k in EXPORT_FIELDS if k not in {"start","end","date","end_date"}}
        item.update(start=begin.astimezone(tz).isoformat(timespec="microseconds"),
                    end=end.astimezone(tz).isoformat(timespec="microseconds"),
                    date=begin.astimezone(tz).date().isoformat(),
                    end_date=end.astimezone(tz).date().isoformat())
        yield item


def write_exports(conn, settings):
    out = settings.db_path.parent / "exports"
    out.mkdir(exist_ok=True, mode=0o700)
    out.chmod(0o700)
    generation="snapshots/"+uuid.uuid4().hex
    snapshot=out/generation
    snapshot.mkdir(parents=True,mode=0o700)
    json_tmp = snapshot / "sessions.json"
    csv_tmp = snapshot / "sessions.csv"
    count = 0
    # Two staged files are generated from the same read transaction under the
    # collector's process lock. manifest.json is published after both replacements.
    with json_tmp.open("w", encoding="utf-8") as jf, csv_tmp.open("w", encoding="utf-8", newline="") as cf:
        json_tmp.chmod(0o600); csv_tmp.chmod(0o600)
        writer = csv.DictWriter(cf, fieldnames=EXPORT_FIELDS)
        writer.writeheader(); jf.write("[\n")
        for item in session_rows(conn, settings.timezone):
            if count: jf.write(",\n")
            jf.write(json.dumps(item, ensure_ascii=False)); writer.writerow(item)
            count += 1
        jf.write("\n]\n")
        for file in (jf, cf): file.flush(); os.fsync(file.fileno())
    manifest={"rows":count,"timezone":settings.timezone.key,"generation":generation,
              "exported_at_utc":now_utc_iso(),"sha256":{
                  p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (json_tmp,csv_tmp)}}
    (snapshot/"manifest.json").write_text(json.dumps(manifest)+"\n")
    (snapshot/"manifest.json").chmod(0o600)
    previous=None
    if (out/"current.json").exists():
        previous=json.loads((out/"current.json").read_text())["generation"]
    manifest_tmp = out / ".manifest.json.tmp"
    manifest_tmp.write_text(json.dumps(manifest)+"\n")
    manifest_tmp.chmod(0o600); manifest_tmp.replace(out / "current.json")
    # Preserve the original pre-snapshot exports once, then publish convenience
    # links. Consumers requiring a consistent pair pin current.json's generation.
    next_link=out/".current.next"
    next_link.symlink_to(generation,target_is_directory=True); next_link.replace(out/"current")
    for name in ("sessions.json","sessions.csv","manifest.json"):
        old=out/name
        if old.exists() and not old.is_symlink():
            legacy=out/"legacy-first-sync"; legacy.mkdir(exist_ok=True,mode=0o700)
            if not (legacy/name).exists(): old.replace(legacy/name)
        if not old.is_symlink(): old.symlink_to("current/"+name)
    # Retain two generated snapshots; SQLite and the transition ledger retain
    # the history. Never remove raw source data or the preserved first export.
    for candidate in (out/"snapshots").iterdir():
        relative="snapshots/"+candidate.name
        if relative in {generation,previous} or len(candidate.name)!=32: continue
        if not all(c in "0123456789abcdef" for c in candidate.name): continue
        if candidate.is_symlink(): continue
        expected={"sessions.json","sessions.csv","manifest.json"}
        if {p.name for p in candidate.iterdir()}!=expected: continue
        for name in expected: (candidate/name).unlink()
        candidate.rmdir()
    return count


def export_snapshot(settings):
    """Pin and verify one immutable generation rather than mixing replacements."""
    out=settings.db_path.parent/"exports"
    manifest=json.loads((out/"current.json").read_text())
    generation=manifest["generation"]
    if not generation.startswith("snapshots/") or len(generation)!=42:
        raise ValueError("invalid export generation")
    if not all(c in "0123456789abcdef" for c in generation[10:]):
        raise ValueError("invalid export generation")
    if set(manifest["sha256"])!={"sessions.csv","sessions.json"}:
        raise ValueError("incomplete export manifest")
    for name,digest in manifest["sha256"].items():
        if name not in {"sessions.csv","sessions.json"}: raise ValueError("invalid export file")
        if hashlib.sha256((out/generation/name).read_bytes()).hexdigest()!=digest:
            raise ValueError("export snapshot hash mismatch")
    return manifest


def reconcile_biome(conn, sources):
    changed=0
    for source in sources:
        if not isinstance(source,ReplayBiomeSource): continue
        for device,events in source.canonical.items():
            keys={event_key(e.device_id,e.start,e.bundle_id) for e in events}
            bundles={}
            for event in events: bundles.setdefault(event.bundle_id,[]).append(event)
            for values in bundles.values(): values.sort(key=lambda e:e.start)
            starts={k:[e.start for e in v] for k,v in bundles.items()}
            for row in conn.execute("SELECT * FROM screen_events WHERE device_id=?",(device,)).fetchall():
                key=row["event_key"]
                active=1
                if key not in keys:
                    begin=parse_timestamp(row["started_at_utc"])
                    end=begin+timedelta(seconds=row["duration_s"])
                    same=bundles.get(row["bundle_id"],[])
                    index=bisect.bisect_left(starts.get(row["bundle_id"],[]),begin)
                    overlap=any(e.start<end and e.end>begin for e in same[max(0,index-1):index+1])
                    # Preserve unmatched historical observations. Supersede only
                    # an interval replaced by overlapping canonical evidence.
                    if overlap: active=0
                old=conn.execute("SELECT active FROM local_event_lifecycle WHERE event_key=?",(key,)).fetchone()
                if old is None or old[0]!=active:
                    conn.execute("INSERT INTO local_event_lifecycle VALUES(?,?,?) ON CONFLICT(event_key) DO UPDATE SET active=excluded.active,changed_at=excluded.changed_at",
                                 (key,active,now_utc_iso()))
                    changed+=1
    conn.commit()
    return changed


def rebuild_active_days(conn,days,tz):
    conn.execute("""CREATE TEMP VIEW screen_events AS SELECT e.* FROM main.screen_events e
      LEFT JOIN main.local_event_lifecycle l USING(event_key) WHERE COALESCE(l.active,1)=1""")
    try: rebuild_days(conn,days,tz)
    finally: conn.execute("DROP VIEW temp.screen_events")


def database_health(settings, *, now=None):
    validate_settings(settings)
    now = now or datetime.now(UTC)
    if not settings.db_path.exists():
        return {"status":"pending","database_present":False,"timezone":settings.timezone.key}
    conn = connect(settings.db_path, read_only=True)
    try:
        platforms = {}
        for row in conn.execute("""SELECT d.platform, COUNT(DISTINCT d.device_id) AS devices,
          COUNT(e.event_key) AS sessions, MIN(e.started_at_utc) AS earliest_start_utc,
          MAX(e.started_at_utc) AS latest_start_utc,
          MAX(strftime('%Y-%m-%dT%H:%M:%fZ',julianday(e.started_at_utc)+e.duration_s/86400.0)) AS latest_end_utc,
          SUM(CASE WHEN e.duration_s>0 AND e.bundle_id<>'' THEN 1 ELSE 0 END) AS valid_intervals,
          SUM(CASE WHEN m.app_name IS NOT NULL THEN 1 ELSE 0 END) AS named_sessions,
          SUM(CASE WHEN m.category IS NOT NULL THEN 1 ELSE 0 END) AS categorized_sessions
          FROM devices d LEFT JOIN local_sessions e USING(device_id)
          LEFT JOIN local_app_metadata m USING(bundle_id) GROUP BY d.platform"""):
            platforms[row["platform"]] = {k:row[k] for k in row.keys() if k!="platform"}
        last = conn.execute("SELECT status,started_at,finished_at,details_json FROM runs WHERE run_type='local_replay' ORDER BY id DESC LIMIT 1").fetchone()
        successes = conn.execute("SELECT COUNT(*) FROM runs WHERE run_type='local_replay' AND status='success'").fetchone()[0]
        first = get_state(conn, "local_initial_success")
        baseline = json.loads(first) if first else None
        due = parse_timestamp(baseline["at_utc"]) + timedelta(hours=24) if baseline else None
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        age=max(0,(now-parse_timestamp(last["started_at"])).total_seconds()) if last else None
        state=last["status"] if last else "pending"
        if state=="success" and age>2700: state="stale"
        runs=[parse_timestamp(r[0]) for r in conn.execute("SELECT started_at FROM runs WHERE run_type='local_replay' AND status='success' AND started_at>=? ORDER BY started_at",
                                                        ((now-timedelta(hours=24)).isoformat(),))]
        left=max(now-timedelta(hours=24),parse_timestamp(baseline["at_utc"])) if baseline else now
        window=[left]+[t for t in runs if t>=left]+[now]
        max_gap=max(((b-a).total_seconds() for a,b in zip(window,window[1:])),default=0)
        result = {"status":state, "database_present":True,
          "last_collector_revision":json.loads(last["details_json"] or "{}").get("collector_revision") if last else None,
          "timezone":settings.timezone.key,"platforms":platforms,"successful_runs":successes,
          "last_run_at_utc":last["started_at"] if last else None,
          "last_run_finished_at_utc":last["finished_at"] if last else None,
          "last_run_age_s":age,"successful_runs_last_24h":len(runs),"max_collection_gap_last_24h_s":max_gap,
          "first_success_at_utc":baseline["at_utc"] if baseline else None,
          "review_24h_due_at_utc":due.isoformat() if due else None,
          "review_24h_due":bool(due and now>=due),"sqlite_quick_check_ok":integrity,
          "duplicate_key_groups":conn.execute("""SELECT COUNT(*) FROM (
            SELECT device_id,started_at_utc,bundle_id FROM local_sessions
            GROUP BY device_id,started_at_utc,bundle_id HAVING COUNT(*)>1)""").fetchone()[0]}
        review = get_state(conn,"local_review_24h")
        result["review_24h"] = json.loads(review) if review else None
        try:
            manifest=export_snapshot(settings)
            result["export_integrity_ok"]=True
            result["exported_sessions"]=manifest["rows"]
        except (OSError,ValueError,KeyError):
            result["export_integrity_ok"]=False
            if result["status"]=="success": result["status"]="export_invalid"
        return result
    finally:
        conn.close()


def collect(settings, *, sources=None):
    validate_settings(settings)
    logger = logging.getLogger("screentime")
    handler = WarningCounter()
    previous = logger.handlers[:], logger.propagate, logger.level
    logger.handlers = [handler]; logger.propagate = False; logger.setLevel(logging.WARNING)
    try:
        with process_lock(db_lock_path(settings.db_path)):
            conn = connect(settings.db_path)
            init_db(conn)
            conn.execute("""CREATE TABLE IF NOT EXISTS local_event_lifecycle (
              event_key TEXT PRIMARY KEY, active INTEGER NOT NULL, changed_at TEXT NOT NULL)""")
            conn.commit()
            started = now_utc_iso()
            run_id = conn.execute("INSERT INTO runs(run_type,status,started_at) VALUES('local_replay','running',?)",(started,)).lastrowid
            conn.commit()
            try:
                if sources is None:
                    if denied_sources(settings): raise PermissionError("protected sources inaccessible")
                    library = Path.home()/"Library"
                    sources = [ReplayBiomeSource(library,ledger=conn), ReplayKnowledgeCSource(library/"Application Support/Knowledge/knowledgeC.db")]
                sources=list(sources)
                taxonomy.OVERRIDES.clear(); taxonomy.apply_overrides(settings.apps)
                previous_days = {r[0] for r in conn.execute("SELECT DISTINCT day FROM daily_device_stats")}
                stats = sync_sources(conn,sources,settings.timezone,device_names=settings.devices,
                                     ignored_devices=settings.ignored_devices,lookup_country=None)
                # Corrections that shorten an interval can remove an old day;
                # the upstream importer rebuilds only the new interval's days.
                reconciled=reconcile_biome(conn,sources)
                if stats.events_inserted or stats.events_updated or reconciled:
                    current_days = {r[0] for r in conn.execute("SELECT DISTINCT day FROM daily_device_stats")}
                    rebuild_active_days(conn,previous_days|current_days,settings.timezone)
                refresh_metadata(conn,settings)
                exported = write_exports(conn,settings)
                problem = bool(stats.errors or stats.sources_failed or stats.events_invalid or handler.count)
                present = {r[0] for r in conn.execute("SELECT DISTINCT platform FROM local_sessions")}
                current_platforms={d.platform for source in sources for d in source.devices()}
                source_data_ok=True
                for source in sources:
                    if isinstance(source,ReplayBiomeSource):
                        source_data_ok &= any(d.platform=="iphone" and source.current_transitions.get(d.id,0)>0
                                              for d in source.devices())
                    elif isinstance(source,ReplayKnowledgeCSource):
                        source_data_ok &= source.current_event_count>0
                problem |= not {"iphone","mac"}.issubset(present)
                problem |= not {"iphone","mac"}.issubset(current_platforms)
                problem |= not source_data_ok
                status = "partial" if problem else "success"
                details = {"mode":"full_retained_replay","source_warnings":handler.count,
                           "collector_revision":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           "source_errors":len(stats.errors),"network_attempts":NETWORK_ATTEMPTS,
                           "exported_sessions":exported,"sources_ok":stats.sources_ok,
                           "sources_failed":stats.sources_failed,"invalid_events":stats.events_invalid,
                           "current_platforms":sorted(current_platforms)}
                details["current_source_data_ok"]=source_data_ok
                if NETWORK_ATTEMPTS: status="failed"
                conn.execute("""UPDATE runs SET status=?,finished_at=?,events_read=?,events_inserted=?,
                  events_updated=?,details_json=? WHERE id=?""",
                  (status,now_utc_iso(),stats.events_read,stats.events_inserted,stats.events_updated,json.dumps(details),run_id))
                if status=="success" and get_state(conn,"local_initial_success") is None:
                    set_state(conn,"local_initial_success",json.dumps({"at_utc":now_utc_iso(),"sessions":exported}))
                conn.commit()
                result=database_health(settings)
                baseline_raw=get_state(conn,"local_initial_success")
                if baseline_raw:
                    baseline=json.loads(baseline_raw)
                    if "platform_latest" not in baseline:
                        baseline["platform_latest"]={k:v["latest_start_utc"] for k,v in result["platforms"].items()}
                        baseline["baseline_captured_at_utc"]=now_utc_iso()
                        set_state(conn,"local_initial_success",json.dumps(baseline)); conn.commit()
                result.update(events_read=stats.events_read,events_inserted=stats.events_inserted,
                              events_updated=stats.events_updated,events_unchanged=stats.events_unchanged,**details)
                if result["review_24h_due"] and status=="success" and not result["review_24h"]:
                    baseline=json.loads(get_state(conn,"local_initial_success"))
                    checks={"sqlite_integrity":result["sqlite_quick_check_ok"],
                            "export_integrity":result["export_integrity_ok"],
                            "no_duplicate_keys":result["duplicate_key_groups"]==0,
                            "historical_rows_retained":exported>=baseline["sessions"],
                            "scheduling_continuity":result["max_collection_gap_last_24h_s"]<=2700,
                            "current_sources_present":{"iphone","mac"}.issubset(current_platforms)}
                    for platform in ("iphone","mac"):
                        old=baseline.get("platform_latest",{}).get(platform)
                        latest=result["platforms"].get(platform,{}).get("latest_start_utc")
                        checks["newer_"+platform+"_activity_seen"]=bool(old and latest and parse_timestamp(latest)>parse_timestamp(old))
                    review={"checked_at_utc":now_utc_iso(),"status":"success" if all(checks.values()) else "partial","checks":checks}
                    set_state(conn,"local_review_24h",json.dumps(review)); conn.commit()
                    result["review_24h"]=review
                return result
            except Exception as exc:
                conn.rollback()
                conn.execute("UPDATE runs SET status='failed',finished_at=?,error=? WHERE id=?",
                             (now_utc_iso(),type(exc).__name__,run_id)); conn.commit()
                raise
            finally:
                conn.close()
    finally:
        logger.handlers, logger.propagate, logger.level = previous


def main(argv=None):
    sys.addaudithook(deny_network)
    os.umask(0o077)
    parser=argparse.ArgumentParser(description="Local-only Screen Time collection; aggregate output only")
    parser.add_argument("command",choices=["collect","health"])
    args=parser.parse_args(argv)
    # Whitelist the config source; inherited sink or source-path overrides must
    # never alter the local-only collector's behavior.
    for key in list(os.environ):
        if key.startswith(("HA_","INFLUX_","SCREENTIME_")): del os.environ[key]
    os.environ["SCREENTIME_CONFIG"]=str(Path.home()/".config/screentime/config.toml")
    try:
        settings=load_settings(); validate_settings(settings)
        if args.command=="collect":
            launchd.rotate_logs()
            result=collect(settings)
        else:
            result=database_health(settings)
            result["denied_source_count"]=len(denied_sources(settings))
            if result["denied_source_count"]:
                result["status"]="permission_required"
            else:
                library=Path.home()/"Library"
                current={d.platform for source in (ReplayBiomeSource(library),
                    ReplayKnowledgeCSource(library/"Application Support/Knowledge/knowledgeC.db")) for d in source.devices()}
                result["current_source_platforms"]=sorted(current)
                if not {"iphone","mac"}.issubset(current): result["status"]="source_unavailable"
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0 if result["status"]=="success" else 1
    except LockBusyError:
        print(json.dumps({"status":"skipped","reason":"collector_already_running"})); return 0
    except Exception as exc:
        print(json.dumps({"status":"failed","error_type":type(exc).__name__})); return 1


if __name__=="__main__":
    raise SystemExit(main())
