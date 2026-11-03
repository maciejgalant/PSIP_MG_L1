<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" styleCategories="Symbology">
  <!-- IOP: 5 rownych przedzialow -1..1 (kolumna iop_klasa liczona w compute_iop.py).
       Kolory: -1 czerwony -> 0 bialy -> 1 zielony. -->
  <renderer-v2 type="categorizedSymbol" attr="iop_klasa" symbollevels="0" enableorderby="0" forceraster="0" referencescale="-1">
    <categories>
      <category value="1" symbol="0" label="-1.0 … -0.6  bardzo trudna" render="true" type="integer"/>
      <category value="2" symbol="1" label="-0.6 … -0.2  trudna" render="true" type="integer"/>
      <category value="3" symbol="2" label="-0.2 … 0.2  przeciętna" render="true" type="integer"/>
      <category value="4" symbol="3" label="0.2 … 0.6  dobra" render="true" type="integer"/>
      <category value="5" symbol="4" label="0.6 … 1.0  bardzo dobra" render="true" type="integer"/>
    </categories>
    <symbols>
      <symbol type="fill" name="0" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="color" type="QString" value="215,25,28,255"/>
            <Option name="outline_color" type="QString" value="110,110,110,255"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="outline_width" type="QString" value="0.1"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="style" type="QString" value="solid"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="fill" name="1" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="color" type="QString" value="235,140,141,255"/>
            <Option name="outline_color" type="QString" value="110,110,110,255"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="outline_width" type="QString" value="0.1"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="style" type="QString" value="solid"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="fill" name="2" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="color" type="QString" value="255,255,255,255"/>
            <Option name="outline_color" type="QString" value="110,110,110,255"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="outline_width" type="QString" value="0.1"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="style" type="QString" value="solid"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="fill" name="3" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="color" type="QString" value="140,202,160,255"/>
            <Option name="outline_color" type="QString" value="110,110,110,255"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="outline_width" type="QString" value="0.1"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="style" type="QString" value="solid"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="fill" name="4" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="color" type="QString" value="26,150,65,255"/>
            <Option name="outline_color" type="QString" value="110,110,110,255"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="outline_width" type="QString" value="0.1"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="style" type="QString" value="solid"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
    <rotation/>
    <sizescale/>
  </renderer-v2>
  <layerGeometryType>2</layerGeometryType>
</qgis>
