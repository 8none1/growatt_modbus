"""Decode a Growatt SPH inverter's holding and input registers into dicts.

These are read-only telemetry decoders shared by the poller. Scaling and
register choices are verified against a real SPH; see REGISTERS.md for the
discrepancies that are deliberately left as-is.

All-or-nothing: if any register read fails (e.g. a dropped/garbled RTU frame from
a raw dongle bridge), the decoder returns None so the caller skips the cycle rather
than publishing partial data.
"""

from .client import read_double_reg, read_holding_registers, read_input_registers


def read_inverter_holding_registers(client):
    holding_registers = {}
    registers = read_holding_registers(client, 0, 16)
    if not registers:
        return None
    holding_registers['safetyFunctionsBitMap']      = registers[1]
    holding_registers['maxOutputActivePower']        = registers[3]
    holding_registers['maxOutputReactivePower']      = registers[4]
    holding_registers['inverterPowerFactor']         = registers[5]
    normal_power                                     = read_double_reg(registers[6], registers[7], 0.1)
    holding_registers['NormalPower']                 = round(normal_power, 2)
    holding_registers['inverterNormalVoltage']       = registers[8]
    holding_registers['firmwareVersionH']            = registers[9]
    holding_registers['firmwareVersionM']            = registers[10]
    holding_registers['firmwareVersionL']            = registers[11]
    holding_registers['controllerVersionH']          = registers[12]
    holding_registers['controllerVersionM']          = registers[13]
    holding_registers['controllerVersionL']          = registers[14]
    holding_registers['lcdLanguage']                 = registers[15]
    next_chunk_start = 122
    registers = read_holding_registers(client, next_chunk_start, 64)
    if not registers:
        return None
    holding_registers['exportLimitState']            = registers[122 - next_chunk_start]
    holding_registers['exportLimitRate']             = registers[123 - next_chunk_start]
    holding_registers['svgFunctionEnabled']          = registers[141 - next_chunk_start]
    holding_registers['numBatteryModules']           = registers[185 - next_chunk_start]
    registers = read_holding_registers(client, 1000, 93)
    if not registers:
        return None
    holding_registers['vbatStopCharge']              = registers[5]
    holding_registers['vbatStopDischarge']           = registers[6]
    # Priority Mode - 0 = load, 1 = Batt, 2 = Grid
    holding_registers['priorityMode']                = registers[44]
    holding_registers['battType']                    = registers[48]
    holding_registers['exportToGridRatePercent']     = registers[70]
    holding_registers['exportToGridStopDischargePercent'] = registers[71]
    holding_registers['batFirstChargeRate']          = registers[90]
    holding_registers['batFirstStopChargeSOC']       = registers[91]
    holding_registers['acChargeEnabled']             = registers[92]
    return holding_registers


def read_inverter_input_registers(client):
    input_registers = {}
    registers = read_input_registers(client, 0, 118)
    if not registers:
        return None
    input_registers['inverterStatus']               = registers[0]  # Seems to be 6 at night
    input_registers['pvPowerTotal']                 = read_double_reg(registers[1], registers[2], 0.1)
    input_registers['pv1Voltage']                   = round(registers[3] * 0.1, 1)
    input_registers['pv1Current']                   = round(registers[4] * 0.1, 1)
    input_registers['pv1Power']                     = round(read_double_reg(registers[5], registers[6], 0.1), 1)
    input_registers['pv2Voltage']                   = round(registers[7] * 0.1, 1)
    input_registers['pv2Current']                   = round(registers[8] * 0.1, 1)
    input_registers['pv2Power']                     = round(read_double_reg(registers[9], registers[10], 0.1), 1)
    input_registers['pvBattPower']                  = round(read_double_reg(registers[35], registers[36], 0.1), 1)
    input_registers['gridFreq']                     = round(registers[37] * 0.01, 3)
    input_registers['gridVolt']                     = round(registers[38] * 0.1, 2)
    input_registers['pvOutputCurrent']              = round(registers[39] * 0.1, 1)  # PV output current, not grid
    input_registers['pvOutputWattsVA']              = round(read_double_reg(registers[40], registers[41], 0.1), 1)
    input_registers['inverterTemperature']          = round(registers[93] * 0.1, 1)
    input_registers['IPMTemperature']               = round(registers[94] * 0.1, 1)
    input_registers['boostTemperature']             = round(registers[95] * 0.1, 1)
    input_registers['inverterPowerFactorNow']       = registers[100]  # 0 -> 20000 range
    input_registers['realOutputPowerPercent']       = registers[101]
    input_registers['OPFullWatt']                   = read_double_reg(registers[102], registers[103], 0.1)
    input_registers['InverterFaultCode']            = registers[105]
    input_registers['FaultBitCode']                 = read_double_reg(registers[106], registers[107])
    input_registers['WarningBitCode']               = read_double_reg(registers[110], registers[111])
    input_registers['ACChargePower']                = read_double_reg(registers[116], registers[117], 0.1)
    # --- Energy counters (kWh, 32-bit, x0.1). Validated live; ideal for the HA energy dashboard. ---
    input_registers['eacToday']                     = round(read_double_reg(registers[53], registers[54], 0.1), 1)
    input_registers['eacTotal']                     = round(read_double_reg(registers[55], registers[56], 0.1), 1)
    input_registers['epv1Today']                    = round(read_double_reg(registers[59], registers[60], 0.1), 1)
    input_registers['epv1Total']                    = round(read_double_reg(registers[61], registers[62], 0.1), 1)
    input_registers['epv2Today']                    = round(read_double_reg(registers[63], registers[64], 0.1), 1)
    input_registers['epv2Total']                    = round(read_double_reg(registers[65], registers[66], 0.1), 1)
    input_registers['epvTotal']                     = round(read_double_reg(registers[91], registers[92], 0.1), 1)
    # --- Diagnostics ---
    input_registers['deratingMode']                 = registers[104]  # 0=none,1=PV,3=Vac,4=Fac,5=Tboost,6=Tinv,7=ctrl,9=overBackByTime
    input_registers['operatingHours']               = round(read_double_reg(registers[57], registers[58]) * 0.5 / 3600, 1)  # reg unit 0.5s
    next_chunk_start = 1000
    registers = read_input_registers(client, next_chunk_start, 124)
    if not registers:
        return None
    input_registers['systemWorkMode']               = registers[0]
    input_registers['dischargePower']               = read_double_reg(registers[9], registers[10], 0.1)
    input_registers['chargePower']                  = read_double_reg(registers[11], registers[12], 0.1)
    input_registers['battVoltage']                  = round(registers[13] * 0.1, 3)
    input_registers['battSOC']                      = registers[14]
    input_registers['gridImportPowerTotal']         = read_double_reg(registers[21], registers[22], 0.1)
    input_registers['gridExportPowerTotal']         = read_double_reg(registers[29], registers[30], 0.1)
    input_registers['pLocalLoadTotal']              = read_double_reg(registers[37], registers[38], 0.1)
    # --- Storage energy counters (kWh, 32-bit, x0.1). Validated live. ---
    input_registers['eToUserToday']                 = round(read_double_reg(registers[44], registers[45], 0.1), 1)
    input_registers['eToUserTotal']                 = round(read_double_reg(registers[46], registers[47], 0.1), 1)
    input_registers['eToGridToday']                 = round(read_double_reg(registers[48], registers[49], 0.1), 1)
    input_registers['eToGridTotal']                 = round(read_double_reg(registers[50], registers[51], 0.1), 1)
    input_registers['eDischargeToday']              = round(read_double_reg(registers[52], registers[53], 0.1), 1)
    input_registers['eDischargeTotal']              = round(read_double_reg(registers[54], registers[55], 0.1), 1)
    input_registers['eChargeToday']                 = round(read_double_reg(registers[56], registers[57], 0.1), 1)
    input_registers['eChargeTotal']                 = round(read_double_reg(registers[58], registers[59], 0.1), 1)
    input_registers['eLocalLoadToday']              = round(read_double_reg(registers[60], registers[61], 0.1), 1)
    input_registers['eLocalLoadTotal']              = round(read_double_reg(registers[62], registers[63], 0.1), 1)
    input_registers['battTemperature']              = registers[40]
    input_registers['epsFreq']                      = registers[67]
    input_registers['epsVolt']                      = round(registers[68] / 10, 2)
    input_registers['epsCurrent']                   = registers[69]
    input_registers['epsPower']                     = read_double_reg(registers[70], registers[71], 0.1)
    input_registers['epsLoadPercent']               = registers[80]
    input_registers['epsPowerFactor']               = registers[81]
    input_registers['bmsStatus']                    = registers[83]
    input_registers['bmsStatusBitmap']              = format(registers[83], '016b')
    #  Bit map for bmsStatusBitmap:
    #  0 & 1 - 00 soft start, 01 stand by, 10 charge, 11 discharge
    #  2 - errors?
    #  3 - cell balance 0 = unbalance, 1 = balance
    #  4 - sleep status 0 disable 1 enable
    #  5 output discharge - 0 disable 1 enable
    #  6 output charge
    #  7 battery terminal - 0 connected, 1 disconnected
    #  8 & 9 operation mode, 00 - stand alone, 01 - parallel, 10 - parallel preparation
    #  10 & 11 SP status - 00 none, 01 standby, 10 charge, 11 discharge
    input_registers['bmsError']                     = registers[85]
    input_registers['bmsSOC']                       = registers[86]
    input_registers['bmsDeltaV']                    = registers[94]
    input_registers['bmsCycleCount']                = registers[95]
    input_registers['bmsSOH']                       = registers[96]
    # --- BMS cell summary (1108-1110 confirmed against the PDF and live data). ---
    # NB: 1108-1123 is NOT a 16-cell voltage array (the old 'cellVoltage1..16' was wrong).
    # The true per-cell voltages live in the battery's own CAN/ESS protocol (0x0071+), not Modbus.
    input_registers['maxCellVoltage']               = round(registers[108] * 0.001, 3)  # V
    input_registers['minCellVoltage']               = round(registers[109] * 0.001, 3)  # V
    input_registers['batteryModuleCount']           = registers[110]
    # 1111-1123: PDF labels these as counts/indices/temps/SOC/error words, but the live values
    # contradict that on this firmware. Captured raw under their address, meaning UNCONFIRMED.
    for addr in range(1111, 1124):
        input_registers["bmsReg" + str(addr)]       = registers[addr - 1000]
    # Storage AC-charge energy and self-consumption energy (separate read; past the 124-register window).
    registers = read_input_registers(client, 1124, 21)  # 1124-1144
    if not registers:
        return None
    input_registers['acChargeEnergyToday']          = round(read_double_reg(registers[0], registers[1], 0.1), 1)
    input_registers['acChargeEnergyTotal']          = round(read_double_reg(registers[2], registers[3], 0.1), 1)
    input_registers['eSelfToday']                   = round(read_double_reg(registers[17], registers[18], 0.1), 1)  # 1141-1142
    input_registers['eSelfTotal']                   = round(read_double_reg(registers[19], registers[20], 0.1), 1)  # 1143-1144
    return input_registers
