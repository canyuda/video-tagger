// Generates the JS-reference-side 对拍 fixture (D29): deterministic
// signatures from the reference implementation (seeded LCG + fixed clock),
// saved for the Python port's compatibility tests.
//
// Usage: node scripts/gen_abogus_fixture.mjs
// Reference lib path is the local DouyinVideoDownloader checkout (docs/07 §5).

import { writeFileSync } from 'node:fs';
import { ABogus } from '/Users/yiqi/webProjects/DouyinVideoDownloader/lib/abogus.js';
import { normalizeQuery, sign } from '/Users/yiqi/webProjects/DouyinVideoDownloader/lib/websign.js';
import { sm3ToArray } from '/Users/yiqi/webProjects/DouyinVideoDownloader/lib/sm3.js';
import { USERAGENT } from '/Users/yiqi/webProjects/DouyinVideoDownloader/lib/api.js';

// Deterministic LCG identical to the Python side (32-bit exact in doubles:
// 1664525 * (2^32-1) + c < 2^53).
function makeLcg(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
}

const NOW_MS = 1759400000000;
const UA = USERAGENT;

const queries = [
  'device_platform=webapp&aid=6383&channel=channel_pc_web&aweme_id=7350000000000000001&count=10&uifid=abc-uifid-123&msToken=' + 'x'.repeat(156),
  'device_platform=webapp&aid=6383&sec_user_id=MS4wLjABAAAA9y04iBlVdeMQqTJbqsQZKb-tqWqWW29jPVJqideHT70&max_cursor=0&count=20&uifid=abc-uifid-123&msToken=' + 'Yz'.repeat(78),
];

const cases = queries.map((query) => {
  const bogus = new ABogus(UA, { rng: makeLcg(42) }).get_value(query, { nowMs: NOW_MS });
  return { query, a_bogus: bogus };
});

const web = {
  normalized: normalizeQuery('aid=6383&channel=channel_pc_web&uifid=abc-uifid-123&msToken=a%2Bb%3Dc'),
  signed: sign('aid=6383&channel=channel_pc_web&uifid=abc-uifid-123', 'abc-uifid-123', { timestamp: 1759400000 }),
};

const fixture = {
  ua: UA,
  now_ms: NOW_MS,
  seed: 42,
  cases,
  websign: web,
  sm3_abc: sm3ToArray('abc'),
  sm3_cjk: sm3ToArray('中文测试'),
};

writeFileSync(
  new URL('../tests/fixtures/abogus_compat.json', import.meta.url),
  JSON.stringify(fixture, null, 2) + '\n',
);
console.log('fixture written: tests/fixtures/abogus_compat.json');
