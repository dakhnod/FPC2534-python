import bleak
import asyncio
import fpc2534

async def main():
    scanner = bleak.BleakScanner()
    
    device = await scanner.find_device_by_name("BLEnky 29:F4:DA", timeout=30)
    
    messages_queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    print(device)
    # return

    def on_disconnect(client):
        asyncio.run_coroutine_threadsafe(messages_queue.put(None), loop)
        # await messages_queue.put(None)
    
    while True:
        async with bleak.BleakClient(device, timeout=30, disconnected_callback=on_disconnect) as client:    
            # await client.connect()
            print("connected")
            
            async def on_data(characteristic, data):
                response = fpc2534.parse_response(data)
                await messages_queue.put(response)
            
            await client.start_notify('383f0002-7947-d815-7830-14f1584109c5', on_data)

            await client.write_gatt_char('383f0001-7947-d815-7830-14f1584109c5', fpc2534.send_request(fpc2534.CMD_RESET), True)
            print(await messages_queue.get())

            await asyncio.sleep(1)
            
            print('identifying')
            payload = fpc2534.identify_finger()
            await client.write_gatt_char('383f0001-7947-d815-7830-14f1584109c5', payload, True)
            while True:
                reply = await messages_queue.get()

                if reply == None:
                    print('disconnected')
                    break
                
                if reply.get('finger_found') is not None:
                    print(f'found finger {reply["template_id"]}')
                elif reply['event'] == 'EVENT_FINGER_LOST':
                    await client.write_gatt_char('383f0001-7947-d815-7830-14f1584109c5', payload, True)
                else:
                    print(f'unexpected reply: {reply}')
    
if __name__ == '__main__':
    asyncio.run(main())