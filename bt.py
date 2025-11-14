import bleak
import asyncio
import fpc2534

async def main():
    scanner = bleak.BleakScanner()
    
    device = await scanner.find_device_by_name("BLEnky nRF21540", timeout=30)
    
    print(device)
    # return
    
    async with bleak.BleakClient(device, timeout=30) as client:    
        # await client.connect()
        print("connected")
        
        def on_data(characteristic, data):
            print(fpc2534.parse_response(data))
        
        client.start_notify('383f0002-7947-d815-7830-14f1584109c5', on_data)
        
        client.write_gatt_char('383f0002-7947-d815-7830-14f1584109c5', fpc2534.send_request(fpc2534.CMD_STATUS), True)
    
if __name__ == '__main__':
    asyncio.run(main())