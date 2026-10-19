import test from 'node:test';
import assert from 'node:assert/strict';
import { vocalGaps, vocalCountdown } from '../web/js/lyric-timing.js';

const line = (start, end) => ({start, end, words:[{t:'Sing',s:start,e:end}]});
const lines = [line(10,15),line(18,23),line(53,59)];

test('counts through an intro and a long instrumental, but not a short breath or outro',()=>{
  const gaps=vocalGaps(lines);
  assert.equal(vocalCountdown(gaps,0),10);
  assert.equal(vocalCountdown(gaps,9.9),1);
  assert.equal(vocalCountdown(gaps,10),null);
  assert.equal(vocalCountdown(gaps,16),null);
  assert.equal(vocalCountdown(gaps,23),30);
  assert.equal(vocalCountdown(gaps,52.9),1);
  assert.equal(vocalCountdown(gaps,53),null);
  assert.equal(vocalCountdown(gaps,62),null);
});
test('seeking in either direction recomputes the remaining time',()=>{
  const gaps=vocalGaps(lines);
  for (const [t,count] of [[50,3],[30,23],[52.8,1],[5,5],[20,null]]) assert.equal(vocalCountdown(gaps,t),count);
});
test('countdown shows elapsed seconds at the current playback speed',()=>{
  const gaps=vocalGaps(lines);
  assert.equal(vocalCountdown(gaps,43,1.25),8);
  assert.equal(vocalCountdown(gaps,43,.8),13);
  assert.equal(vocalCountdown(gaps,43,0),10);
});
test('lyric offsets shift the countdown and also apply to the intro',()=>{
  const offset=3;
  const shifted=vocalGaps([line(2,4),line(20,22)],offset);
  assert.equal(vocalCountdown(shifted,0-offset),5);
  assert.equal(vocalCountdown(shifted,4.9-offset),1);
  assert.equal(vocalCountdown(shifted,5-offset),null);
  assert.equal(vocalCountdown(shifted,13-offset),10);
});
test('overlapping duet vocals do not produce a false instrumental break',()=>{
  const gaps=vocalGaps([line(0,30),line(5,8),line(20,25),line(35,40)]);
  assert.equal(vocalCountdown(gaps,10),null);
  assert.equal(vocalCountdown(gaps,29),null);
  assert.equal(vocalCountdown(gaps,31),4);
});
test('uses word timing for a break inside a line and falls back to line timing',()=>{
  const words={start:0,end:30,words:[{s:0,e:3,t:'First'},{s:20,e:23,t:'Second'}]};
  assert.equal(vocalCountdown(vocalGaps([words]),10),10);
  assert.equal(vocalCountdown(vocalGaps([{start:10,end:20}]),5),5);
  assert.equal(vocalCountdown(vocalGaps([]),5),null);
});
