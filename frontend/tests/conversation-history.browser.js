async (page) => {
  page.setDefaultTimeout(2000);
  await page.setViewportSize({width: 1440, height: 1000});
  await page.unroute('**/api/query');
  const base = 'http://127.0.0.1:15173';
  const results = [];
  let pending = null;
  const body = {risk_score: 0, risk_level: 'LOW', citations: [], warnings: [], answer_status: 'ANSWERED', irac_sections: {issue: 'Test', rule: 'Test', application: 'Test', conclusion: 'Kết quả kiểm thử'}};
  await page.route('**/api/query', async route => {
    if (pending) return pending(route);
    await route.fulfill({json: body});
  });
  const reset = async conversations => {
    await page.goto(base);
    await page.evaluate(conversations => {
      localStorage.clear();
      if (conversations) localStorage.setItem('vat_public_convs', JSON.stringify(conversations));
    }, conversations);
    await page.reload();
    const openSidebar = page.getByTitle('Mở thanh bên', {exact: true});
    if (await openSidebar.isVisible()) await openSidebar.click();
  };
  const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem('vat_public_convs') || '[]'));
  const submit = async question => {
    await page.locator('textarea').fill(question);
    await page.locator('textarea').press('Enter');
  };
  const waitResult = async () => {
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('vat_public_convs') || '[]').some(c => c.messages.some(m => m.result)), null, {timeout: 2500});
  };
  const check = async (name, fn) => {
    try { await fn(); results.push({name, passed: true}); }
    catch (error) { results.push({name, passed: false, error: error.message}); }
  };
  const assert = (value, message) => { if (!value) throw new Error(message); };
  await check('first send survives reload', async () => {
    await reset(); await submit('Câu hỏi đầu tiên'); await waitResult(); await page.reload();
    assert((await stored())[0]?.messages[0]?.q === 'Câu hỏi đầu tiên', 'First conversation not stored');
    assert(await page.getByText('Kết quả kiểm thử', {exact: true}).count() > 0, 'Result not restored');
  });
  await check('empty new chat survives reload', async () => {
    await reset(); await page.getByTitle('Tạo cuộc hội thoại mới').click();
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('vat_public_convs') || '[]').length === 1, null, {timeout: 2500});
    await page.reload(); assert((await stored()).length === 1 && !(await stored())[0].messages.length, 'Empty chat lost');
  });
  await check('explicit chat result survives reload and deletion', async () => {
    await reset(); await page.getByTitle('Tạo cuộc hội thoại mới').click(); await submit('Câu hỏi riêng'); await waitResult();
    await page.reload(); assert((await stored())[0]?.messages[0]?.q === 'Câu hỏi riêng', 'Explicit chat lost');
    const oldId = (await stored())[0].id; await page.getByTitle('Xóa hội thoại', {exact: true}).first().locator('..').hover(); await page.getByTitle('Xóa hội thoại', {exact: true}).first().click(); await page.reload();
    assert(!(await stored()).some(c => c.id === oldId), 'Deleted chat resurrected');
  });
  for (const failure of [false, true]) {
    for (const remove of [false, true]) {
      await check(`deferred ${failure ? 'error' : 'success'} after ${remove ? 'delete' : 'switch'}`, async () => {
        await reset([{id:'origin',title:'Origin',updated:2,messages:[]},{id:'other',title:'Other',updated:1,messages:[]}]);
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        pending = async route => { await gate; if (failure) await route.fulfill({status:503,json:{detail:'test error'}}); else await route.fulfill({json:body}); };
        await submit('Câu hỏi đang chờ');
        await page.waitForTimeout(100);
        if (remove) {
          await page.getByTitle('Xóa hội thoại', {exact:true}).first().locator('..').hover();
          await page.getByTitle('Xóa hội thoại', {exact:true}).first().click();
        } else await page.getByText('Other', {exact:true}).click();
        release(); pending = null;
        await page.waitForTimeout(1800);
        assert(await page.locator('main').getByText('Câu hỏi đang chờ', {exact:true}).count() === 0, 'Late response overwrote selected chat');
        const data = await stored();
        if (remove) assert(!data.some(c => c.id === 'origin'), 'Deleted origin revived');
        else {
          const result = data.find(c => c.id === 'origin')?.messages[0]?.result;
          assert(result, 'Surviving origin missed result');
          assert(failure ? result.score === 100 && result.conclusion.includes('Không thể kết nối') : result.conclusion === 'Kết quả kiểm thử', 'Wrong completion stored');
        }
      });
    }
  }
  for (const action of ['new', 'delete inactive', 'delete last']) {
    await check(`deferred response after ${action}`, async () => {
      await reset(action === 'delete last' ? [{id:'origin',title:'Origin',updated:2,messages:[]}]
        : [{id:'origin',title:'Origin',updated:2,messages:[]},{id:'other',title:'Other',updated:1,messages:[]}]);
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      pending = async route => { await gate; await route.fulfill({json:body}); };
      await submit('Câu hỏi đang chờ'); await page.waitForTimeout(100);
      if (action === 'new') await page.getByTitle('Tạo cuộc hội thoại mới').click();
      else {
        const button = page.getByTitle('Xóa hội thoại', {exact:true}).nth(action === 'delete inactive' ? 1 : 0);
        await button.locator('..').hover(); await button.click();
      }
      release(); pending = null; await page.waitForTimeout(1800);
      const data = await stored();
      if (action === 'delete last') assert(!data.some(c => c.id === 'origin'), 'Last conversation revived');
      else assert(data.find(c => c.id === 'origin')?.messages[0]?.result?.conclusion === 'Kết quả kiểm thử', 'Origin result missing');
      if (action !== 'delete inactive') assert(await page.locator('main').getByText('Câu hỏi đang chờ', {exact:true}).count() === 0, 'New chat overwritten');
      else assert(!data.some(c => c.id === 'other'), 'Inactive conversation revived');
      await page.reload();
      assert((await stored()).length === data.length, 'Deletion/new-chat persistence changed after reload');
    });
  }
  await check('settings remain unchanged', async () => {
    await reset();
    const settings = await page.evaluate(() => localStorage.getItem('vat_public_settings'));
    await submit('Kiểm tra cấu hình'); await waitResult(); await page.reload();
    assert(await page.evaluate(() => localStorage.getItem('vat_public_settings')) === settings, 'Settings changed');
  });
  await page.unroute('**/api/query');
  return results;
}
