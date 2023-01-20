import os
import re
import ssl
import httpx
import certifi
import asyncio
import aiohttp
import aiofiles
from lxml import html
from urllib.parse import urlparse, parse_qs
from requests_toolbelt import MultipartEncoder
import requests

# Movie ID eg:3937
c = 3937 
# Override height 
# eg:(480, 1080, 2160...) 0 to disable
# uses max feame height by default
h = 480
# Specify disc ids (integers) uses all available by default if left empty
# eg: ['9770', '9781']
selected_discs = ['9770', '9781']

image_temp_dir = 'img_tmp'
concurrent_downloads = 10

# ----------------------------------------------

async def gather_pooled(n, *coros):
    sem = asyncio.Semaphore(n)
    async def sem_coro(coro):
        async with sem:
            return await coro
    return await asyncio.gather(
        *[sem_coro(c) for c in coros],
        return_exceptions=True)

# ----------------------------------------------

async def disc_info(c: int):
    print('Fetching info...')
    url = f'https://caps-a-holic.com/c_list.php?c={c}'
    res = requests.get(url)
    tree = html.fromstring(res.content)
    main_title = tree.xpath("//div[@class='big-header']/text()")[0]

    if 'd1=' in res.url:
        d1id = parse_qs(urlparse(res.url).query)['d1'][0]
        d2id = parse_qs(urlparse(res.url).query)['d2'][0]
        info_comp_title = tree.xpath("//div[@class='c-cell' and contains(@style, '400')]")
        d1 = info_comp_title[0].xpath("./text()")
        d2 = info_comp_title[1].xpath("./text()")

        return {
            d1id: [f"{d1[0]} {d1[1]}", re.findall('\d+x\d+', d1[-1])[-1]],
            d2id: [f"{d2[0]} {d2[1]}", re.findall('\d+x\d+', d2[-1])[-1]]
        }, main_title

    discs = tree.xpath("//div[contains(@id, 'd_')]")
    disc_info = dict()
    for disc in discs:
        title = disc.xpath('.//text()')
        id = disc.get('id').replace('d_', '')
        disc_info[id] = title
    return disc_info, main_title

# ----------------------------------------------

async def resolve_images(d1: str, d2: str):
    # only returns images for d1
    images = []
    url = f'https://caps-a-holic.com/c.php?\
        d1={d1}&d2={d2}&c={c}'
    async with httpx.AsyncClient() as client:
        res = await client.get(url)
    tree = html.fromstring(res.content)
    image_links = tree.xpath("//a[contains(@href, 'c.php?d1=')]")
    for link in image_links:
        images.append(
            parse_qs(
                urlparse(link.get('href')).query)['s1'][0])
    return (d1, images)

# ----------------------------------------------

async def gather_images(disc_ids):
    print('Fetching image IDs...')
    images = dict()
    tasks = []
    for i in range(len(disc_ids)-1):
        d1 = disc_ids[i]
        d2 = disc_ids[i+1]
        tasks.append(resolve_images(d1, d2))
    tasks.append(resolve_images(d2, d1))
    results = await asyncio.gather(*tasks)
    for disc, img_id_list in results:
        images[disc] = img_id_list
    return images

# ----------------------------------------------

async def fetch_file(id, url):
    fname = f'{id}.png'
    ssl_context = ssl.create_default_context(
        cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(
        connector=conn
    ) as session:
        async with session.get(url) as resp:
            assert resp.status == 200
            data = await resp.read()

    async with aiofiles.open(
        os.path.join(image_temp_dir, fname), "wb"
    ) as outfile:
        await outfile.write(data)

# ----------------------------------------------

async def grab_images(images: dict, height, image_temp_dir):
    print('Downloading images...')
    tasks = []
    os.makedirs(image_temp_dir, exist_ok=True)
    for value in images.values():
        for img_id in value:
            img_dl = f'https://caps-a-holic.com/c_image.php?\
                max_height={height}&s={img_id}&a=0&x=0&y=0&l=1'
            tasks.append(fetch_file(img_id, img_dl))
    await gather_pooled(concurrent_downloads, *tasks)

# ----------------------------------------------

async def slowpics_comparison(
    comp_title, disc_info, image_data, img_dir='img_tmp'):
    print('Uploading...')
    post_data = {
        'collectionName': (None, comp_title),
        'hentai': (None, 'false'),
        'optimizeImages': (None, 'false'),
        'public': (None, 'false')
    }
    z = zip(*image_data.values())
    open_files = []
    for i, item in enumerate(z):
        for j, imgid in enumerate(item):
            disc = disc_info[list(image_data.keys())[j]][0]
            post_data[f'comparisons[{i}].images[{j}].name'] = (
                None, f"{disc} | {imgid}"
            )
            f = open(
                os.path.join(img_dir, f"{imgid}.png"), 'rb')
            open_files.append(f)
            post_data[f'comparisons[{i}].images[{j}].file'] = (
                f"{imgid}.png", f, 'image/png'
            )
        
    with requests.Session() as client:
        client.get("https://slow.pics/api/comparison")
        files = MultipartEncoder(post_data)
        length = str(files.len)
        headers = {
            "Content-Length": length,
            "Content-Type": files.content_type,
            "X-XSRF-TOKEN": client.cookies.get_dict()["XSRF-TOKEN"]
        }
        response = client.post(
            "https://slow.pics/api/comparison", 
            data=files, headers=headers, verify=False)
        print(f'https://slow.pics/c/{response.text}')

    for f in open_files:
        f.close()

# ----------------------------------------------

async def start_process():
    global selected_discs, image_temp_dir
    image_temp_dir = os.path.abspath(image_temp_dir)
    info, main_title = await disc_info(c)
    height = 0
    disc_data = []
    if selected_discs == []:
        selected_discs = info.keys()
    for d_id in selected_discs:
        d_id = str(d_id)
        if not d_id in info: continue
        disc_data.append(d_id)
        height = max(height, int(info[d_id][1].rsplit('x', maxsplit=1)[-1]))
    
    if h: height = h
    print(f'Height: {height}')
    images = await gather_images(disc_data)
    await grab_images(images, height, image_temp_dir)
    await slowpics_comparison(main_title, info, images)

# ----------------------------------------------

asyncio.run(start_process())
