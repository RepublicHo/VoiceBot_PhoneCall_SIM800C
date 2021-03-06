# Updated version with advanced NLP features, including t2s, s2t
from __future__ import division
import io
import os
import re
import sys
import time
import wave
import queue
import serial
import pyaudio
import threading
import serial.tools.list_ports
from pygame import mixer
from concurrent.futures import ThreadPoolExecutor
from google.cloud import texttospeech
from google.cloud import speech
from six.moves import queue
from datetime import datetime

# 引入 requests 模組
import requests

# Audio recording parameters
RATE = 16000
CHUNK = int(RATE/10) # 100ms

executor = ThreadPoolExecutor(max_workers=16)
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'ambient-sum-352109-87d42557e70d.json' # plz modify the name if needed
config_serialDeviceName = 'USB-SERIAL'
config_phoneNumber = '51153639'
phonenum = ''
stop_signal = False
client = texttospeech.TextToSpeechClient()
speech_client = speech.SpeechClient()
audio_temp_folder = 'audio_temp/'


class MicrophoneStream(object):
    """Opens a recording stream as a generator yielding the audio chunks."""

    def __init__(self, rate, chunk):
        self._rate = rate
        self._chunk = chunk

        # Create a thread-safe buffer of audio data
        self._buff = queue.Queue()
        self.closed = True

    def __enter__(self):
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = self._audio_interface.open(
            format=pyaudio.paInt16,
            # The API currently only supports 1-channel (mono) audio
            # https://goo.gl/z757pE
            channels=1,
            rate=self._rate,
            input=True,
            frames_per_buffer=self._chunk,
            # Run the audio stream asynchronously to fill the buffer object.
            # This is necessary so that the input device's buffer doesn't
            # overflow while the calling thread makes network requests, etc.
            stream_callback=self._fill_buffer,
        )

        self.closed = False

        return self

    def __exit__(self, type, value, traceback):
        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        # Signal the generator to terminate so that the client's
        # streaming_recognize method will not block the process termination.
        self._buff.put(None)
        self._audio_interface.terminate()

    def _fill_buffer(self, in_data, frame_count, time_info, status_flags):
        """Continuously collect data from the audio stream, into the buffer."""
        self._buff.put(in_data)
        return None, pyaudio.paContinue

    def generator(self):
        while not self.closed:
            # Use a blocking get() to ensure there's at least one chunk of
            # data, and stop iteration if the chunk is None, indicating the
            # end of the audio stream.
            chunk = self._buff.get()
            if chunk is None:
                return
            data = [chunk]

            # Now consume whatever other data's still buffered.
            while True:
                try:
                    chunk = self._buff.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break

            yield b"".join(data)



def AI_Enquiry(transcript, language_code, phonenum):
     # get result from kimia AI 
    # 使用 GET 方式下載普通網頁
    
    requestURL = 'https://kimia.toyokoexpress.com/chat/?text='+ transcript +'&kiosk_type=17&session=' + str(phonenum)
    print(f"Request: {requestURL}")
    r = requests.get(requestURL)

    # 檢查狀態碼是否 OK
    if r.status_code == requests.codes.ok:
        print("OK")

    # 輸出網頁 HTML 原始碼
    print(r.text)
    if r.text != "":
        return r.text
    else:
        if language_code == "en-us" or language_code == "en-uk":
            return "Sorry, I don't understand your question."
        elif language_code == "zh" or language_code == "cmn-hans-cn" or language_code == "zh-TW":
            return "對不起，我不明白你的問題。"
        else:
            return "對唔住，我唔知你講咩。"

def listen_print_save_loop(responses, stream, phonenum):
    # print("called!!!")
    # print(responses[0].results)
    """Iterates through server responses, then prints and saves them.

    The responses passed is a generator that will block until a response
    is provided by the server.

    Each response may contain multiple results, and each result may contain
    multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
    print/save only the transcription for the top alternative of the top result.
    
    In this case, responses are provided for interim results as well. If the
    response is an interim one, print a line feed at the end of it, to allow
    the next result to overwrite it, until the response is a final one. For the
    final one, print a newline to preserve the finalized transcription.
    """
    num_chars_printed = 0
    for response in responses:
        if not response.results:
            continue

        # The `results` list is consecutive. For streaming, we only care about
        # the first result being considered, since once it's `is_final`, it
        # moves on to considering the next utterance.
        result = response.results[0]
        if not result.alternatives:
            continue

        # Display the transcription of the top alternative.
        transcript = result.alternatives[0].transcript

        # Display interim results, but with a carriage return at the end of the
        # line, so subsequent lines will overwrite them.
        #
        # If the previous result was longer than this one, we need to print
        # some extra spaces to overwrite the previous result
        stream.closed = True  # off mic

        # get result from kimia AI
        string = AI_Enquiry(transcript, result.language_code, phonenum)

        # executor.submit(text2speech, str(r.text), result.language_code)
        text2speech(string, result.language_code)
        print(result.language_code)
        if result.language_code == "en-us" or result.language_code == "en-uk":
            print("Reply: What can I help you?")
            text2speech("What can I help you?", result.language_code)
        elif result.language_code == "zh" or result.language_code == "cmn-hans-cn" or result.language_code == "zh-TW":
            print("Reply: 請問還有什麼可以幫到你?")
            text2speech("請問還有什麼可以幫你?", result.language_code)
        else:
            print("Reply: 請問重有咩可以幫你?")
            text2speech("請問重有咩可以幫你?", result.language_code)

        overwrite_chars = " " * (num_chars_printed - len(transcript))

        if not result.is_final:
            sys.stdout.write(transcript + overwrite_chars + "\r")
            sys.stdout.flush()
            num_chars_printed = len(transcript)

        else:
            print(f"Transcript: {transcript + overwrite_chars}")
            print(f"Language code: {result.language_code}")
            print(f"Confidence: {result.alternatives[0].confidence:.0%}")

            # Exit recognition if any of the transcribed phrases could be
            # one of our keywords.
            if re.search(r"\b(exit|quit)\b", transcript, re.I):
                print("Exiting..")
                break

            num_chars_printed = 0


def speech2text(phonenum):
    # See http://g.co/cloud/speech/docs/languages
    # for a list of supported languages.
    primary_language = "yue-Hant-HK"  # a BCP-47 language tag
    secondary_language1 = "en-US"
    secondary_language2 = "zh"
    global stop_signal

    client = speech.SpeechClient()
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        speech_contexts=[speech.SpeechContext(phrases=["$ORDINAL"])],
        language_code=primary_language,
        alternative_language_codes=[secondary_language1, secondary_language2],
        use_enhanced=True,
        # A model must be specified to use enhanced model.
        model="command_and_search"
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=config, interim_results=False, single_utterance=True
    )

    # text2speech("您好, 我係人工智能服務大使Kimia, 請問有咩可以幫到您呢? 請輸入數字選擇故障類別: 1. 前臺電腦故障 2. 前臺電腦週邊設備故障 3. 後臺電腦故障 4. 後臺電腦週邊設備故障 5. 手持, 顯示幕或其他故障", "yue-Hant-HK")
    text2speech("請問有咩可以幫你?", "yue-Hant-HK")

    while True:
        if stop_signal == False:
            try:
                # text2speech("請說出你的問題", "yue-Hant-HK")
                with MicrophoneStream(RATE, CHUNK) as stream:
                    audio_generator = stream.generator()
                    requests = (
                        speech.StreamingRecognizeRequest(streaming_config=streaming_config, audio_content=content)
                        for content in audio_generator
                    )
                    print(requests)
                    # responses = client.streaming_recognize(streaming_config, requests, timeout = 7)
                    responses = client.streaming_recognize(streaming_config, requests)
                    print(responses)
                    # Now, put the transcription responses to use.
                    listen_print_save_loop(responses, stream, phonenum)
            except:
                continue
        else:
            stop_signal = False
            break


def text2speech(text, language_code):
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice1 = texttospeech.VoiceSelectionParams(
        language_code=language_code, 
        ssml_gender=texttospeech.SsmlVoiceGender.FEMALE
    )

    # print(client.list_voices())

    audio_config = texttospeech.AudioConfig(
        audio_encoding= texttospeech.AudioEncoding.MP3
    )

    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice1,
        audio_config=audio_config
    )

    isExist = os.path.exists(audio_temp_folder)
    if not isExist:
        # Create a new directory because it does not exist 
        os.makedirs(audio_temp_folder)
        print("The new directory is created!")

    date_string = datetime.now().strftime("%d%m%Y%H%M%S")
    # write response to the audio file
    with open(audio_temp_folder+'result_'+date_string+'.mp3', 'wb') as output:
        output.write(response.audio_content)

    # Play the audio file to let the user hear the sound
    pl = PlayMP3(audio_temp_folder+'result_'+date_string+'.mp3')
    pl.play()

# Play mp3 files, which is converted from the text using GCP API. 
class PlayMP3():
    # Constructor to assign the fileName, which is the mp3 file to play
    def __init__(self, name):
        self._filename = name

    def play(self):
        mixer.init()
        mixer.music.load(self._filename)
        # print("* recording")
        mixer.music.play()
        print("The mp3 should be played")
        while mixer.music.get_busy():  # wait for music to finish playing
            time.sleep(1)
        mixer.music.stop()
        mixer.quit()

        # delete all files inside folder audio_temp
        for f in os.listdir(audio_temp_folder):
            os.remove(os.path.join(audio_temp_folder, f))

def run_sim800c():
    global phonenum, stop_signal
    language_code = "yue-Hant-HK"
    port_list = list(serial.tools.list_ports.comports())
    dialed = False

    # print("Debug info\nA works")
    print("The port number is: " + str(len(port_list)))
    if len(port_list) == 0:
        print("\nno port can be used :(")
        exit(0)
    else:
        # print('\nB works')
# print all available port name
#         for i in port_list:
#             print(i)

    # find the correct port for data transmission
        for i in port_list:
            if str(i).find(config_serialDeviceName) != -1:
                s = serial.Serial(i.device, 115200, timeout=0)
        #s = serial.Serial(port_list[0].device, 115200, timeout=0.5)
        # print('\nC works')
        sio = io.TextIOWrapper(io.BufferedRWPair(s, s))

        # print("\nD works")
        # sio.write(f'AT+DDET=1\nATS0=2\nATE1\nAT+COLP=1\nATD{str(phonenum)};\n')
        sio.write(f'AT+DDET=1\nATS0=2\nATE1\nAT+COLP=1\nAT+CLIP=1\n')
        ''' 
        AT+DDET=1: enable DTMF detection

        ATS0=2: Set Number of Rings before Automatically Answering the Call

        ATE1: 用於設置開啓回顯模式，檢測Module與串口是否連通，能否接收AT命令
        開啓回顯，有利於調試
        
        AT+COLP=1: 開啓被叫號碼顯示，即成功撥通的時候（被叫接聼電話），模塊會
        返回被叫號碼      
        
        ATD電話號碼;:用於撥打電話號碼
        '''

        sio.flush()
        # print("\nE works")
        # print("Calling (If it cannot work for long, please use XCOM V2.0 to check)....")
        print("Waiting for call (If it cannot work for long, please use XCOM V2.0 to check)....")
        while 1:
            # print(sio.readlines()) it leads to a big problem
            try:
                x = "".join(sio.readlines())
            except Exception:
                print("\nError occurs accidentally, check the port or other devices :(")
                exit()

            # Detect status 
            # print(x)

            # Dailed
            if x.find('+COLP: \"') != -1:
                print("\ndialed")
                executor.submit(speech2text, phonenum)

            if x.find('+DTMF: 1') != -1:
                string = AI_Enquiry("1號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 2') != -1:
                string = AI_Enquiry("2號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 3') != -1:
                string = AI_Enquiry("3號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 4') != -1:
                string = AI_Enquiry("4號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 5') != -1:
                string = AI_Enquiry("5號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 6') != -1:
                string = AI_Enquiry("6號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 7') != -1:
                string = AI_Enquiry("7號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 8') != -1:
                string = AI_Enquiry("8號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 9') != -1:
                string = AI_Enquiry("9號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: 0') != -1:
                string = AI_Enquiry("0號", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: #') != -1:
                string = AI_Enquiry("你好", language_code, phonenum)
                executor.submit(text2speech, string, "yue-Hant-HK")
            elif x.find('+DTMF: *') != -1:
                print("\nDTMF:*")

            if x.find('NO CARRIER') != -1:
                print("\nRing off")
                dialed = False
                stop_signal = True
                # Stop audio recording after the end of the call
                # executor.submit(audio_thread.stop)

            if (x.find('BUSY') != -1) | (x.find('NO ANSWER') != -1):
                print("\nHe/She hangs up")
                break

            if (x.find('ERROR') != -1): 
                print("\nErrors occurr in SIM card (it's not China Mobile card or it arrears), \nor in other devices, \nor Card installation error")
                break

            if dialed == False:
                if (x.find('+CLIP: "') != -1):
                    phonenum = int(x[x.find('+CLIP: "')+8:x.find('+CLIP: "')+16])
                    print(str(phonenum) + " called in")
                    sio.write('ATA\n')
                    time.sleep(10)
                    dialed = True
                    print("\ndialed")
                    
                    executor.submit(speech2text, phonenum)
                    # executor.submit(speech2text, phonenum)

def main():
    print("   _____ _____ __  __  ___   ___   ___   _____   ____   ____ _______ ")
    print("  / ____|_   _|  \/  |/ _ \ / _ \ / _ \ / ____| |  _ \ / __ \__   __|")
    print(" | (___   | | | \  / | (_) | | | | | | | |      | |_) | |  | | | |   ")
    print("  \___ \  | | | |\/| |> _ <| | | | | | | |      |  _ <| |  | | | |   ")
    print("  ____) |_| |_| |  | | (_) | |_| | |_| | |____  | |_) | |__| | | |   ")
    print(" |_____/|_____|_|  |_|\___/ \___/ \___/ \_____| |____/ \____/  |_|   ")
    print("")

    run_sim800c()

    # uncomment to test without phone
    # it is also used in altspace
    # speech2text(config_phoneNumber)

    print("Done")
    os._exit(1)


if __name__ == "__main__":
    main()
