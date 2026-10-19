import test from 'node:test';
import assert from 'node:assert/strict';
import { computeGains, playableStems, stemMix } from '../web/js/mix.js';

const song = {stems:[{id:'lead'}, {id:'backing',replacedBy:['backing_harmony','backing_other']},
  {id:'backing_harmony',parent:'backing'}, {id:'backing_other',parent:'backing'}, {id:'drums'}]};
const harmony = song.stems[2];
const performance = {guide:.15,backing:true};

test('overlapping harmonies follow both lead fader and guide while other backing stays full',()=>{
  const mix={stems:{lead:{vol:.8}}};
  for (const guide of [0,.15,.5,1]) {
    const gains=computeGains(song,mix,{...performance,guide});
    assert.equal(gains.backing_harmony.gain,gains.lead.gain);
    assert.equal(gains.backing_harmony.gain,.8*guide);
    assert.equal(gains.backing_other.gain,1);
    assert.equal(gains.drums.gain,1);
    assert.equal(gains.backing,undefined); // Never play original + split copies together.
  }
});
test('existing mix snapshots inherit backing effects, level and mute without migration',()=>{
  const mix={stems:{backing:{vol:.4,pan:-.3,reverb:.2,tone:'warm'}}};
  assert.equal(stemMix(harmony,mix).followLead,true);
  const gains=computeGains(song,mix,performance);
  assert.equal(gains.backing_other.gain,.4);
  assert.equal(gains.backing_harmony.gain,.15);
  assert.equal(gains.backing_harmony.pan,-.3);
  assert.equal(gains.backing_other.reverb,.2);
  mix.stems.backing.mute=true;
  assert.equal(computeGains(song,mix,performance).backing_harmony.gain,0);
  assert.equal(computeGains(song,mix,performance).backing_other.gain,0);
});
test('unlinked harmonies have independent volume and follow the backing on/off control',()=>{
  const mix={stems:{backing_harmony:{followLead:false,vol:.6}}};
  assert.equal(computeGains(song,mix,{guide:0}).backing_harmony.gain,.6);
  const gains=computeGains(song,mix,{guide:.7,backing:false});
  assert.equal(gains.backing_harmony.gain,0);
  assert.equal(gains.backing_other.gain,0);
  assert.equal(gains.lead.gain,.7);
});
test('solo permits auditioning the harmony alone, and explicit mute wins',()=>{
  const mix={stems:{lead:{vol:.7},backing_harmony:{solo:true}}};
  const gains=computeGains(song,mix,performance,{karaoke:false});
  assert.equal(gains.lead.gain,0);
  assert.equal(gains.backing_harmony.gain,.7);
  assert.equal(gains.backing_other.gain,0);
  mix.stems.backing_harmony.mute=true;
  assert.equal(computeGains(song,mix,performance).backing_harmony.gain,0);
});
test('muting the lead silences linked harmonies, unless a dry lead is active',()=>{
  const mix={stems:{lead:{mute:true},lead_dry:{mute:false,vol:.5}}};
  assert.equal(computeGains(song,mix,performance).backing_harmony.gain,0);
  const drySong={stems:[...song.stems,{id:'lead_dry',parent:'lead'}]};
  assert.equal(computeGains(drySong,mix,performance).backing_harmony.gain,.075);
});
test('quiet complementary backing remains playable; empty drum kit pieces are skipped',()=>{
  const ids=playableStems({stems:[...song.stems,{id:'quiet_backing',parent:'backing',quiet:true},
    {id:'toms',parent:'drums',quiet:true}]}).map(s=>s.id);
  assert.ok(ids.includes('quiet_backing'));
  assert.ok(!ids.includes('toms'));
  assert.ok(!ids.includes('backing'));
});
test('legacy and fast-quality songs keep their existing vocal behavior',()=>{
  const old={stems:[{id:'lead'},{id:'backing'},{id:'drums'}]};
  assert.equal(computeGains(old,{},performance).lead.gain,.15);
  assert.equal(computeGains(old,{},performance).backing.gain,1);
  assert.equal(computeGains(old,{},performance,{karaoke:false}).lead.gain,1);
});
