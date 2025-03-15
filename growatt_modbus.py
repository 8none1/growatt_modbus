#!/usr/bin/env python3

import time
import json
import datetime
from pymodbus.client import ModbusTcpClient

# Modbus TCP Configuration
MODBUS_DEVICES = [
    {"host": "ew11-1.whizzy.org", "port": 502, "unit": 1},
    {"host": "ew11-2.whizzy.org", "port": 502, "unit": 1}
]

def read_double_reg(r1, r2, multiplier=1):
    value = (r1 << 16 | r2)
    value = value * multiplier
    return value

def read_holding_registers(client, start_address, count):
    """Read holding registers from Modbus."""
    try:
        response = client.read_holding_registers(address=start_address, count=count)
        if response.isError():
            print(f"Error reading Modbus registers {start_address}-{start_address+count}")
            return None
        return response.registers
    except Exception as e:
        print(f"Modbus error: {e}")
        raise
        return None

def read_input_registers(client, start_address, count):
    """Read input registers from Modbus."""
    try:
        response = client.read_input_registers(address=start_address, count=count)
        if response.isError():
            print(f"Error reading Modbus input registers {start_address}-{start_address+count}")
            return None
        return response.registers
    except Exception as e:
        print(f"Modbus error: {e}")
        raise
        return None

def get_inverter_serial_number(client):
    """Read inverter serial number from Modbus."""
    registers = read_holding_registers(client, 23, 5)
    if registers:
        try:
            serial_number = ''.join(chr((i >> 8) & 0xFF) + chr(i & 0xFF) for i in registers)
            return serial_number
        except:
            return "unknown_serial"
    return "unknown_serial"

def get_inverter_time(client):
    registers = read_holding_registers(client, 45, 7)
    year, month, day, hour, minute, second, dow = registers
    print(registers)
    inverter_now = datetime.datetime(year, month, day, hour, minute, second)
    return inverter_now

def read_inverter_holding_registers(client):
    holding_registers ={}
    registers = read_holding_registers(client, 0, 16)
    if registers:
        holding_registers['safetyFunctionsBitMap']      = registers[1]
        holding_registers['maxOutputActivePower']       = registers[3]
        holding_registers['maxOutputReactivePower']     = registers[4]
        holding_registers['inverterPowerFactor']        = registers[5]
        normal_power = (registers[6]<< 16 | registers[7]) * 0.1
        holding_registers['NormalPower']                = round(normal_power, 1)
        holding_registers['inverterNormalVoltage']      = registers[8]
        holding_registers['firmwareVersionH']           = registers[9]
        holding_registers['firmwareVersionM']           = registers[10]
        holding_registers['firmwareVersionL']           = registers[11]
        holding_registers['controllerVersionH']         = registers[12]
        holding_registers['controllerVersionM']         = registers[13]
        holding_registers['controllerVersionL']         = registers[14]
        holding_registers['lcdLanguage']                = registers[15]
    next_chunk_start = 122
    registers = read_holding_registers(client, next_chunk_start, 64)
    if registers:
        holding_registers['exportLimitState']           = registers[122-next_chunk_start]
        holding_registers['exportLimitRate']            = registers[123-next_chunk_start]
        holding_registers['svgFunctionEnabled']         = registers[141-next_chunk_start]
        holding_registers['numBatteryModules']          = registers[185-next_chunk_start]
    next_chunk_start = 241
    registers = read_holding_registers(client, next_chunk_start, 3)
    if registers:
        holding_registers['inverterLng']                = registers[241-next_chunk_start]
        holding_registers['inverterLat']                = registers[242-next_chunk_start]
    registers = read_holding_registers(client, 1000, 93)
    if registers:
        holding_registers['vbatStopCharge']             = registers[5]
        holding_registers['vbatStopDischarge']          = registers[6]
        holding_registers['battMdlSerialNum']           = registers[14]
        holding_registers['battMdlParallNum']           = registers[15]
        #Priority Mode - 0 = load, 1 = Batt, 2 = Grid
        holding_registers['priorityMode']               = registers[44]
        holding_registers['battType']                   = registers[48]
        holding_registers['exportToGridRatePercent']    = registers[70]
        holding_registers['exportToGridStopDischargePercent'] = registers[71]
        # 1080 - 1088 : time blocks for grid first
        # 1090 - 1108 : time blocks for batt first
        # 1110 - 1118 : time blocks for load first
        holding_registers['batFirstChargeRate']         = registers[90]
        holding_registers['batFirstStopChargeSOC']      = registers[91]
        holding_registers['acChargeEnabled']            = registers[92]
    next_chunk_start = 125
    registers = read_holding_registers(client, next_chunk_start, 16)
    if registers:
        holding_registers['batt1SerialNum8']            = registers[125-next_chunk_start]
        holding_registers['batt1SerialNum7']            = registers[126-next_chunk_start]
        holding_registers['batt1SerialNum6']            = registers[127-next_chunk_start]
        holding_registers['batt1SerialNum5']            = registers[128-next_chunk_start]
        holding_registers['batt1SerialNum4']            = registers[129-next_chunk_start]
        holding_registers['batt1SerialNum3']            = registers[130-next_chunk_start]
        holding_registers['batt1SerialNum2']            = registers[131-next_chunk_start]
        holding_registers['batt1SerialNum1']            = registers[132-next_chunk_start]
        holding_registers['batt2SerialNum8']            = registers[133-next_chunk_start]
        holding_registers['batt2SerialNum7']            = registers[134-next_chunk_start]
        holding_registers['batt2SerialNum6']            = registers[135-next_chunk_start]
        holding_registers['batt2SerialNum5']            = registers[136-next_chunk_start]
        holding_registers['batt2SerialNum4']            = registers[137-next_chunk_start]
        holding_registers['batt2SerialNum3']            = registers[138-next_chunk_start]
        holding_registers['batt2SerialNum2']            = registers[139-next_chunk_start]
        holding_registers['batt2SerialNum1']            = registers[140-next_chunk_start]
    return holding_registers

def read_inverter_input_registers(client):
    input_registers ={}
    registers = read_input_registers(client, 0, 117)
    if registers:
        input_registers['inverterStatus']         = registers[0]  # Seems to be 6 at night
        input_registers['pvPowerTotal']           = read_double_reg(registers[1], registers[2], 0.1)
        input_registers['pv1Voltage']             = round(registers[3] * 0.1, 1)
        input_registers['pv1Current']             = round(registers[4] * 0.1, 1)
        input_registers['pv1Power']               = read_double_reg(registers[5], registers[6], 0.1)
        input_registers['pv2Voltage']             = round(registers[7] * 0.1, 1)
        input_registers['pv2Current']             = round(registers[8] * 0.1, 1)
        input_registers['pv2Power']               = read_double_reg(registers[9], registers[10], 0.1)
        input_registers['pvBattPower']            = read_double_reg(registers[35], registers[36], 0.1)
        input_registers['gridFreq']               = round(registers[37] * 0.01, 3)
        input_registers['gridVolt']               = round(registers[38] * 0.1, 2)
        input_registers['pvOutputCurrent']        = round(registers[39] * 0.1, 1)  # This seems to be PV output current, not grid
        input_registers['pvOutputWattsVA']        = read_double_reg(registers[40], registers[41], 0.1)
        input_registers['inverterTemperature']    = registers[93] * 0.1
        input_registers['IPMTemperature']         = round(registers[94] * 0.1, 1)  # What is an IPM?
        input_registers['boostTemperature']       = round(registers[95] * 0.1, 1)
        input_registers['inverterPowerFactorNow'] = registers[100]  # Says 0 -> 20000 which is odd. Surely max PF is 1?
        input_registers['realOutputPowerPercent'] = registers[101]
        input_registers['OPFullWatt']             = read_double_reg(registers[102], registers[103], 0.1)  # Seems to be nothing?
        input_registers['InverterFaultCode']      = registers[105]
        input_registers['FaultBitCode']           = read_double_reg(registers[106], registers[107])
        input_registers['WarningBitCode']         = read_double_reg(registers[110], registers[111])
        input_registers['ACChargePower']          = read_double_reg(registers[116], registers[107], 0.1)
    next_chunk_start = 1000
    registers = read_input_registers(client, next_chunk_start, 124)
    if registers:
        input_registers['systemWorkMode']         = registers[0]
        input_registers['dischargePower']         = read_double_reg(registers[9], registers[10], 0.1)
        input_registers['chargePower']            = read_double_reg(registers[11], registers[12], 0.1)
        input_registers['battVoltage']            = round(registers[13] * 0.1, 3)
        input_registers['battSOC']                = registers[14]
        input_registers['gridImportPowerTotal']   = read_double_reg(registers[21], registers[22], 0.1)
        input_registers['gridExportPowerTotal']   = read_double_reg(registers[29], registers[30],  0.1)
        input_registers['pLocalLoadTotal']        = read_double_reg(registers[37], registers[38], 0.1)
        input_registers['battTemperature']        = registers[40]
        input_registers['epsFreq']                = registers[67]
        input_registers['epsVolt']                = round(registers[68] / 10, 2)
        input_registers['epsCurrent']             = registers[69]
        input_registers['epsPower']               = read_double_reg(registers[70], registers[71], 0.1)
        input_registers['epsLoadPercent']         = registers[80]
        input_registers['epsPowerFactor']         = registers[81]
        input_registers['bmsStatus']              = registers[83]
        input_registers['bmsStatusBitmap']        = format(registers[83], '016b')
        #  Bit map for ^
        #  0 & 1 - 00 soft start, 01 stand by, 10 charge, 11 discharge
        #  2 - errors?
        #  3 - cell balance 0 = unbalance, 1 = balance
        #  4 - sleep status 0 disable 1 enable
        #  5 output discharge - 0 disable 1 enable
        #  6 output charge
        #  7 battery terminal - 0 connected, 1 disconnected
        #  8 & 9 operation mode, 00 - stand alone, 01 - parallel, 10 - parallel preperation
        #  10 & 11 SP status - 00 none, 01 standby, 10 charge, 11 discharge
        input_registers['bmsError']               = registers[85]
        input_registers['bmsSOC']                 = registers[86]
        input_registers['bmsDeltaV']              = registers[94]
        input_registers['bmsCycleCount']          = registers[95]
        input_registers['bmsSOH']                 = registers[96]
        # BMS Cell state
        for i in range(108, 124):
            cell = "cellVoltage" + str(i - 107)
            input_registers[cell] = registers[i]
    return input_registers

def main():
    clients = [ModbusTcpClient(host=dev["host"], port=dev["port"]) for dev in MODBUS_DEVICES]
  
    for idx, client in enumerate(clients):
        client.connect() # Should catch and test the return state here
        serial_number = get_inverter_serial_number(client)
        print(f"Serial number: {serial_number}")
        inverter_time = get_inverter_time(client)
        print(f"Inverter time: {inverter_time}")

        holding_registers = read_inverter_holding_registers(client)
        print(json.dumps(holding_registers, indent=4))

        input_registers = read_inverter_input_registers(client)
        print(json.dumps(input_registers, indent=4))

        client.close()
        
if __name__ == "__main__":
    main()
