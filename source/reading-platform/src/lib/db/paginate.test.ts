import { describe, it, expect } from "vitest";
import { fetchAllRows } from "./paginate";
describe("fetchAllRows", () => {
 it("does not truncate exactly 1000 rows and continues through later pages", async () => {
  const ranges: number[][] = [];
  const result = await fetchAllRows(async (from, to) => {
   ranges.push([from,to]);
   return {data: Array.from({length: from===0 ? 1000 : 17}, (_,i)=>from+i),error:null};
  });
  expect(result).toHaveLength(1017);
  expect(result[1016]).toBe(1016);
  expect(ranges).toEqual([[0,999],[1000,1999]]);
 });
 it("does not silently return a partial result on second-page failure", async () => {
  const failure = new Error("database unavailable");
  await expect(fetchAllRows(async from => from===0
   ? {data:Array(1000).fill(1),error:null} : {data:null,error:failure})).rejects.toBe(failure);
 });
 it("handles an empty page after an exact multiple", async () => {
  expect(await fetchAllRows(async from => ({data:from===0?Array(1000).fill(1):[],error:null}))).toHaveLength(1000);
 });
});
