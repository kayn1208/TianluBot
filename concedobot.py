# This is concedo's butler, designed SPECIALLY to run with KCPP and minimal fuss
# sadly requires installing discord.py, python-dotenv and requests
# but should be very easy to use.

# it's very hacky and very clunky now, so use with caution

# Configure credentials in .env

import discord
import requests
import os, threading, time, random, io, base64, json
from dotenv import load_dotenv
import urllib

load_dotenv()

if not os.getenv("KAI_ENDPOINT") or not os.getenv("BOT_TOKEN") or not os.getenv("ADMIN_NAME"):
    print("Missing .env variables. Cannot continue.")
    exit()

intents = discord.Intents.all()
client = discord.Client(command_prefix="!", intents=intents)
ready_to_go = False
busy = threading.Lock() # a global flag, never handle more than 1 request at a time
submit_endpoint = os.getenv("KAI_ENDPOINT") + "/api/v1/generate"
imggen_endpoint = os.getenv("KAI_ENDPOINT") + "/sdapi/v1/txt2img"
admin_name = os.getenv("ADMIN_NAME")
maxlen = 300

class BotChannelData(): #key will be the channel ID
    def __init__(self, chat_history, bot_reply_timestamp):
        self.chat_history = chat_history # containing an array of messages
        self.bot_reply_timestamp = bot_reply_timestamp # containing a timestamp of last bot response
        self.bot_hasfilter = True # apply nsfw text filter to image prompts
        self.bot_idletime = 120
        self.bot_botloopcount = 0
        self.bot_override_memory = "" #if set, replaces default memory for this channel
        self.bot_override_backend = "" #if set, replaces default backend for this channel

# bot storage
bot_data = {} # a dict of all channels, each containing BotChannelData as value and channelid as key
wi_db = {}

def export_config():
    wls = []
    for key, d in bot_data.items():
        wls.append({"key":key,"bot_idletime":d.bot_idletime,"bot_override_memory":d.bot_override_memory,"bot_override_backend":d.bot_override_backend})
    script_directory = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_directory, 'botsettings.json')
    with open(file_path, 'w') as file:
        json.dump(wls, file, indent=2)

def import_config():
    try:
        script_directory = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_directory, 'botsettings.json')
        if os.path.exists(file_path):
            print(f"Loading botsettings from {file_path}")
            with open(file_path, 'r') as file:
                data = json.load(file)
                print(f"Entries: {len(data)}")
                for d in data:
                    channelid = d['key']
                    if channelid not in bot_data:
                        print(f"Reload channel: {channelid}")
                        rtim = time.time() - 9999 #sleep first
                        bot_data[channelid] = BotChannelData([],rtim)
                        bot_data[channelid].bot_idletime = int(d['bot_idletime'])
                        bot_data[channelid].bot_override_memory = d['bot_override_memory']
                        bot_data[channelid].bot_override_backend = d['bot_override_backend']
        else:
            print("No saved botsettings found.")
    except Exception as e:
        print("Failed to read settings")


def concat_history(channelid):
    global bot_data
    currchannel = bot_data[channelid]
    prompt = ""
    for msg in currchannel.chat_history:
        prompt += "### " + msg + "\n"
    prompt += "### " + client.user.display_name + ":\n"
    return prompt

def prepare_wi(channelid):
    global bot_data,wi_db
    currchannel = bot_data[channelid]
    scanprompt = ""
    addwi = ""
    for msg in (currchannel.chat_history)[-3:]: #only consider the last 3 messages for wi
        scanprompt += msg + "\n"
    scanprompt = scanprompt.lower()
    for keystr, value in wi_db.items():
        rawkeys = keystr.lower().split(",")
        keys = [word.strip() for word in rawkeys]
        for k in keys:
            if k in scanprompt:
                addwi += f"\n{value}"
                break
    return addwi

def append_history(channelid,author,text):
    global bot_data
    currchannel = bot_data[channelid]
    if len(text) > 1000: #each message is limited to 1k chars
        text = text[:1000] + "..."
    msgstr = f"{author}:\n{text}"
    currchannel.chat_history.append(msgstr)
    print(f"{channelid} msg {msgstr}")

    if len(currchannel.chat_history) > 20: #limited to last 20 msgs
        currchannel.chat_history.pop(0)

def prepare_img_payload(channelid, prompt):
    payload = {
        "prompt": prompt,
        "sampler_name": "Euler a",
        "batch_size": 1,
        "n_iter": 1,
        "steps": 20,
        "cfg_scale": 7,
        "width": 512,
        "height": 512,
        "negative_prompt": "ugly, deformed, poorly, censor, blurry, lowres, malformed, watermark, duplicated, grainy, distorted, signature",
        "do_not_save_samples": True,
        "do_not_save_grid": True,
        "enable_hr": False,
        "eta": 0,
        "s_churn": 0,
        "s_tmax": 0,
        "s_tmin": 0,
        "s_noise": 1,
        "override_settings": {
            "sd_model_checkpoint": "imgmodel",
            "eta_noise_seed_delta": 0,
            "CLIP_stop_at_last_layers": 1,
            "ddim_discretize": "uniform",
            "img2img_fix_steps": False,
            "sd_hypernetwork": "None",
            "inpainting_mask_weight": 1,
            "initial_noise_multiplier": 1,
            "comma_padding_backtrack": 20
        }
    }
    return payload

def prepare_payload(channelid):
    global widb, maxlen
    basememory = f"```yaml\ncharacter:\n  name: \"{client.user.display_name}\"\n  species: \"Pixiu\"\n  appearance:\n    fur: \"mostly white with cyan accents\"\n    head_ornament: \"golden forehead horn\"\n    tail: \"fluffy, cyan\"\n    eyes: \"green\"\n  personality:\n    traits:\n      - \"childish\"\n      - \"playful\"\n      - \"hot-headed\"\n      - \"brash\"\n      - \"rebellious\"\n      - \"caring (to those he loves)\"\n      - \"arrogant yet vulnerable\"\n      - \"mischievous\"\n      - \"naive\"\n    appetite: \"insatiable, specifically eats valuable non-edible objects\"\n  description: |\n    {client.user.display_name}, also known as pipi, is a pixiu that loves to eat anything valuable (gold, jewelry, gems, etc.). He is best friends with Sibuxiang (A deer spirit).\n```\n[The following is a chat message log of {client.user.display_name}, conversing/roleplaying with characters. Do not use emojis. Do not use markdown.]\n"
    intromemory = f"\n### {client.user.display_name}:\nHi there! I’m Tianlu, people call me Pipi :3 *Smiles*"
    
    memory = basememory
    # inject world info here
    wi = prepare_wi(channelid)
    if wi!="":
        memory += f"[{client.user.display_name} Summarized Memory Database:{wi}]\n"
    memory += intromemory

    currchannel = bot_data[channelid]
    if currchannel.bot_override_memory!="":
        memory = currchannel.bot_override_memory

    prompt = concat_history(channelid)
    payload = {
    "n": 1,
    "max_context_length": 8192,
    "max_length": maxlen,
    "rep_pen": 1.07,
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 64,
    "top_a": 0,
    "typical": 1,
    "tfs": 1,
    "rep_pen_range": 360,
    "rep_pen_slope": 0.7,
    "sampler_order": [6,0,1,3,4,2,5],
    "min_p": 0,
    "genkey": "KCPP8888",
    "memory": memory,
    "prompt": prompt,
    "quiet": True,
    "trim_stop": True,
    "stop_sequence": [
        "\n###",
        "### "
    ],
    "use_default_badwordsids": False
    }

    return payload

def prepare_vision_payload(b64img):
    global maxlen
    payload = {
    "n": 1,
    "max_context_length": 8192,
    "max_length": maxlen,
    "rep_pen": 1.07,
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 64,
    "top_a": 0,
    "typical": 1,
    "tfs": 1,
    "rep_pen_range": 360,
    "rep_pen_slope": 0.7,
    "sampler_order": [6,0,1,3,4,2,5],
    "min_p": 0,
    "genkey": "KCPP8888",
    "memory": "",
    "images": [b64img],
    "prompt": "### Instruction:\nPlease describe the image in detail, and include transcriptions of any text if found.\n\n### Response:\n",
    "quiet": True,
    "trim_stop": True,
    "stop_sequence": [
        "\n###",
        "### "
    ],
    "use_default_badwordsids": False
    }

    return payload

def detect_nsfw_text(input_text):
    import re
    pattern = r'\b(cock|ahegao|hentai|uncensored|lewd|cocks|deepthroat|deepthroating|dick|dicks|cumshot|lesbian|fuck|fucked|fucking|sperm|naked|nipples|tits|boobs|breasts|boob|breast|topless|ass|butt|fingering|masturbate|masturbating|bitch|blowjob|pussy|piss|asshole|dildo|dildos|vibrator|erection|foreskin|handjob|nude|penis|porn|vibrator|virgin|vagina|vulva|threesome|orgy|bdsm|hickey|condom|testicles|anal|bareback|bukkake|creampie|stripper|strap-on|missionary|clitoris|clit|clitty|cowgirl|fleshlight|sex|buttplug|milf|oral|sucking|bondage|orgasm|scissoring|railed|slut|sluts|slutty|cumming|cunt|faggot|sissy|anal|anus|cum|semen|scat|nsfw|xxx|explicit|erotic|horny|aroused|jizz|moan|rape|raped|raping|throbbing|humping|underage|underaged|loli|pedo|pedophile|prepubescent|shota|underaged)\b'
    matches = re.findall(pattern, input_text, flags=re.IGNORECASE)
    return True if matches else False

@client.event
async def on_ready():
    global ready_to_go
    import_config()
    print("Logged in as {0.user}".format(client))
    ready_to_go = True


@client.event
async def on_message(message):
    global ready_to_go, bot_data, maxlen

    if not ready_to_go:
        return

    channelid = message.channel.id

    # handle admin only commands
    if message.author.name.lower() == admin_name.lower():
        if message.clean_content.startswith("/botwhitelist") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid not in bot_data:
                print(f"Add new channel: {channelid}")
                rtim = time.time() - 9999 #sleep first
                bot_data[channelid] = BotChannelData([],rtim)
                await message.channel.send(f"Channel added to the whitelist. Ping me to talk.")
            else:
                await message.channel.send(f"Channel already whitelisted previously. Please blacklist and then whitelist me here again.")

        elif message.clean_content.startswith("/botblacklist") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid in bot_data:
                del bot_data[channelid]
                print(f"Remove channel: {channelid}")
                await message.channel.send("Channel removed from the whitelist, I will no longer reply here.")

        elif message.clean_content.startswith("/botmaxlen ") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid in bot_data:
                try:
                    oldlen = maxlen
                    newlen = int(message.clean_content.split()[1])
                    maxlen = newlen
                    print(f"Maxlen: {channelid} to {newlen}")
                    await message.channel.send(f"Maximum response length changed from {oldlen} to {newlen}.")
                except Exception as e:
                    maxlen = 250
                    await message.channel.send(f"Sorry, the command failed.")
        elif message.clean_content.startswith("/botidletime ") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid in bot_data:
                try:
                    oldval = bot_data[channelid].bot_idletime
                    newval = int(message.clean_content.split()[1])
                    bot_data[channelid].bot_idletime = newval
                    print(f"Idletime: {channelid} to {newval}")
                    await message.channel.send(f"Idle timeout changed from {oldval} to {newval}.")
                except Exception as e:
                    bot_data[channelid].bot_idletime = 120
                    await message.channel.send(f"Sorry, the command failed.")
        elif message.clean_content.startswith("/botfilteroff") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid in bot_data:
                bot_data[channelid].bot_hasfilter = False
                await message.channel.send(f"Image prompts will no longer be filtered.")
        elif message.clean_content.startswith("/botfilteron") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid in bot_data:
                bot_data[channelid].bot_hasfilter = True
                await message.channel.send(f"Text-filter will be applied to image prompts.")
        elif message.clean_content.startswith("/botsavesettings") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid in bot_data:
                export_config()
                await message.channel.send(f"Bot config saved.")
        elif message.clean_content.startswith("/botmemory ") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid in bot_data:
                try:
                    memprompt = message.clean_content
                    memprompt = memprompt.replace('/botmemory ','')
                    memprompt = memprompt.replace(f'@{client.user.display_name}','')
                    memprompt = memprompt.replace(f'@{client.user.name}','').strip()
                    bot_data[channelid].bot_override_memory = memprompt
                    print(f"BotMemory: {channelid} to {memprompt}")
                    if memprompt=="":
                        await message.channel.send(f"Bot memory override for this channel cleared.")
                    else:
                        await message.channel.send(f"New bot memory override set for this channel.")
                except Exception as e:
                    await message.channel.send(f"Sorry, the command failed.")
        elif message.clean_content.startswith("/botbackend ") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
            if channelid in bot_data:
                try:
                    bbe = message.clean_content
                    bbe = bbe.replace('/botbackend ','')
                    bbe = bbe.replace(f'@{client.user.display_name}','')
                    bbe = bbe.replace(f'@{client.user.name}','').strip()
                    bot_data[channelid].bot_override_backend = bbe
                    print(f"BotBackend: {channelid} to {bbe}")
                    if bbe=="":
                        await message.channel.send(f"Bot backend override for this channel cleared.")
                    else:
                        await message.channel.send(f"New bot backend override set for this channel.")
                except Exception as e:
                    await message.channel.send(f"Sorry, the command failed.")

    # gate before nonwhitelisted channels
    if channelid not in bot_data:
       return

    currchannel = bot_data[channelid]

    # commands anyone can use
    if message.clean_content.startswith("/botsleep") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
        instructions=[
        'Entering sleep mode. Ping me to wake me up again.']
        ins = random.choice(instructions)
        currchannel.bot_reply_timestamp = time.time() - 9999
        await message.channel.send(ins)
    elif message.clean_content.startswith("/botstatus") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
        if channelid in bot_data:
            print(f"Status channel: {channelid}")
            lastreq = int(time.time() - currchannel.bot_reply_timestamp)
            lockmsg = "busy generating a response" if busy.locked() else "awaiting any new requests"
            await message.channel.send(f"I am currently online and {lockmsg}. The last request from this channel was {lastreq} seconds ago.")
    elif message.clean_content.startswith("/botreset") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
        if channelid in bot_data:
            currchannel.chat_history = []
            currchannel.bot_reply_timestamp = time.time() - 9999
            print(f"Reset channel: {channelid}")
            instructions=[
            "Cleared bot conversation history in this channel."
            ]
            ins = random.choice(instructions)
            await message.channel.send(ins)
    elif message.clean_content.startswith("/botdescribe ") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
        if channelid in bot_data:
            uploadedimg = None
            try:
                if message.attachments:
                    for attachment in message.attachments:
                        if attachment.content_type and 'image' in attachment.content_type:
                            print(f"Fetching image: {attachment.url}")
                            req = urllib.request.Request(attachment.url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'})
                            with urllib.request.urlopen(req, timeout=30) as response:
                                temp = response.read()
                                uploadedimg = base64.b64encode(temp).decode('utf-8')
                                print("Image fetched")
            except Exception as e:
                print(f"Error: {e}")
                pass
            if busy.acquire(blocking=False):
                try:
                    if not uploadedimg:
                        await message.channel.send("Sorry, no image was uploaded.")
                    else:
                        await message.channel.send("Attempting to describe the provided image, please wait.")
                        async with message.channel.typing():
                            currchannel.bot_reply_timestamp = time.time()
                            payload = prepare_vision_payload(uploadedimg)
                            print(payload)
                            sep = (submit_endpoint if currchannel.bot_override_backend=="" else currchannel.bot_override_backend)
                            response = requests.post(sep, json=payload)
                            result = ""
                            if response.status_code == 200:
                                result = response.json()["results"][0]["text"]
                            else:
                                print(f"ERROR: response: {response}")
                                result = ""
                            #no need to clean result, if all formatting goes well
                            if result!="":
                                await message.channel.send(f"Image Description: {result}")
                            else:
                                await message.channel.send("Sorry, the image transcription failed!")
                finally:
                    busy.release()
    elif message.clean_content.startswith("/botdraw ") and (client.user in message.mentions or f'@{client.user.name}' in message.clean_content):
        if channelid in bot_data:
            if busy.acquire(blocking=False):
                try:
                    if currchannel.bot_hasfilter and detect_nsfw_text(message.clean_content):
                        await message.channel.send(f"Sorry, the image prompt filter prevents me from drawing this image.")
                    else:
                        await message.channel.send(f"I will attempt to draw your image. Please stand by.")
                        async with message.channel.typing():
                            # keep awake on any reply
                            currchannel.bot_reply_timestamp = time.time()
                            genimgprompt = message.clean_content
                            genimgprompt = genimgprompt.replace('/botdraw ','')
                            genimgprompt = genimgprompt.replace(f'@{client.user.display_name}','')
                            genimgprompt = genimgprompt.replace(f'@{client.user.name}','').strip()
                            print(f"Gen Img: {genimgprompt}")
                            payload = prepare_img_payload(channelid,genimgprompt)
                            response = requests.post(imggen_endpoint, json=payload)
                            result = ""
                            if response.status_code == 200:
                                imgs = response.json()["images"]
                                if imgs and len(imgs) > 0:
                                    result = imgs[0]
                            else:
                                print(f"ERROR: response: {response}")
                                result = ""
                            if result:
                                print(f"Convert and upload file...")
                                file = discord.File(io.BytesIO(base64.b64decode(result)),filename='drawimage.png')
                                if file:
                                    await message.channel.send(file=file)
                finally:
                    busy.release()


    # handle regular chat messages
    if message.author == client.user or message.clean_content.startswith(("/")):
        return

    currchannel = bot_data[channelid]

    append_history(channelid,message.author.display_name,message.clean_content)

    is_reply_to_bot = (message.reference and message.reference.resolved.author == client.user)
    mentions_bot = client.user in message.mentions
    contains_bot_name = (client.user.display_name.lower() in message.clean_content.lower()) or (client.user.name.lower() in message.clean_content.lower())
    is_reply_someone_else = (message.reference and message.reference.resolved.author != client.user)

    #get the last message we sent time in seconds
    secsincelastreply = time.time() - currchannel.bot_reply_timestamp

    if message.author.bot:
        currchannel.bot_botloopcount += 1
    else:
        currchannel.bot_botloopcount = 0

    if currchannel.bot_botloopcount > 4:
        return
    elif currchannel.bot_botloopcount == 4:
        if secsincelastreply < currchannel.bot_idletime:
            await message.channel.send("It appears that I am stuck in a conversation loop with another bot or AI. I will refrain from replying further until this situation resolves.")
        return

    if not is_reply_someone_else and (secsincelastreply < currchannel.bot_idletime or (is_reply_to_bot or mentions_bot or contains_bot_name)):
        if busy.acquire(blocking=False):
            try:
                async with message.channel.typing():
                    # keep awake on any reply
                    currchannel.bot_reply_timestamp = time.time()
                    payload = prepare_payload(channelid)
                    print(payload)
                    sep = (submit_endpoint if currchannel.bot_override_backend=="" else currchannel.bot_override_backend)
                    response = requests.post(sep, json=payload)
                    result = ""
                    if response.status_code == 200:
                        result = response.json()["results"][0]["text"]
                    else:
                        print(f"ERROR: response: {response}")
                        result = ""

                    #no need to clean result, if all formatting goes well
                    if result!="":
                        for I in currchannel.chat_history:
                            result = result.split(I.split("\n")[0])[0]
                        append_history(channelid,client.user.display_name,result)
                        await message.channel.send(result)

            finally:
                busy.release()

try:
    client.run(os.getenv("BOT_TOKEN"))
except discord.errors.LoginFailure:
    print("\n\nBot failed to login to discord")
